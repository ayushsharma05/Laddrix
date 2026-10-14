"""Putting it together: one AccountRuntime per broker account, inside one Platform.

    submit ─► OrderManager ─► gate = RiskGate then OrderThrottle ─► Broker
                 │ events
                 ├─► EventLog          (audit, restart)
                 └─► PositionBook ─► DailyLossWatch ─► kill switch
    market data ─► PositionBook.mark
    broker positions ◄─► PositionReconciler

The order of the gates matters: risk first (it refuses without using any of the
rate limit), then the throttle (which counts what it lets through).
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mocktrade.broker.base import Broker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.gate import Gates
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.positions.book import PositionBook
from mocktrade.positions.reconcile import PositionReconciler
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.checks import RiskGate
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.risk.loss_watch import DailyLossWatch
from mocktrade.risk.throttle import OrderThrottle
from mocktrade.store.events import EventLog


@dataclass
class AccountRuntime:
    account: BrokerAccount
    broker: Broker
    orders: OrderManager
    positions: PositionBook
    risk: RiskGate
    throttle: OrderThrottle
    switch: KillSwitch
    reconciler: PositionReconciler
    loss_watch: DailyLossWatch | None


@dataclass
class Platform:
    instruments: InstrumentStore
    market_data: MarketData
    firm_switch: KillSwitch
    event_log: EventLog | None = None
    accounts: dict[str, AccountRuntime] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_log is not None:
            self.firm_switch.on_change(self.event_log.record_kill)

    def account(self, account_id: str) -> AccountRuntime | None:
        return self.accounts.get(account_id)

    def mark(self, tick: Tick) -> None:
        """Send a tick to every account's positions (market data listener)."""
        for runtime in self.accounts.values():
            runtime.positions.mark(tick)

    async def run(self, *, reconcile: bool = True) -> None:
        """Run every background loop until cancelled: each account's broker updates and
        position checks, and marking positions to the latest prices."""
        loops = [self._mark_loop()]
        for runtime in self.accounts.values():
            loops.append(runtime.orders.run())
            if reconcile:
                loops.append(runtime.reconciler.run())
        await asyncio.gather(*loops)

    async def _mark_loop(self) -> None:
        """Watch the prices of every instrument anyone holds; mark positions as they move."""
        subscription = self.market_data.subscribe()
        try:
            while True:
                held = {
                    p.instrument_id
                    for runtime in self.accounts.values()
                    for p in runtime.positions.positions()
                }
                subscription.add(held - subscription.instrument_ids)
                try:
                    batch = await asyncio.wait_for(subscription.next_batch(), 1.0)
                except TimeoutError:
                    continue
                for tick in batch:
                    self.mark(tick)
        finally:
            subscription.close()

    def add_account(
        self,
        account: BrokerAccount,
        broker: Broker,
        *,
        limits: RiskLimits | None = None,
        ids: ClientIds,
        auto_kill_on_loss: bool = False,
        throttle: OrderThrottle | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> AccountRuntime:
        if account.id in self.accounts:
            raise ValueError(f"account {account.id} is already running")
        limits = limits or RiskLimits()
        switch = KillSwitch(f"account {account.id}", clock=clock)
        book = PositionBook(account.id, clock=clock)
        holder: dict[str, OrderManager] = {}  # the gate needs the manager it guards
        risk = RiskGate(
            limits,
            instruments=self.instruments.get,
            prices=self.market_data.latest,
            position=book.net_qty,
            working=lambda: holder["orders"].working(),
            day_pnl_paise=book.day_pnl_paise,
            switches=(self.firm_switch, switch),
            clock=clock,
        )
        throttle = throttle or OrderThrottle()
        orders = OrderManager(
            account.id,
            broker,
            self.instruments.get,
            ids=ids,
            gate=Gates([risk, throttle]),
            clock=clock,
        )
        holder["orders"] = orders
        orders.subscribe(book.on_order_event)
        if self.event_log is not None:
            orders.subscribe(self.event_log.record)
            switch.on_change(self.event_log.record_kill)
        loss_watch = None
        if auto_kill_on_loss:
            loss_watch = DailyLossWatch(
                book.day_pnl_paise, limits.max_daily_loss_paise, switch, [orders]
            )
            book.subscribe(loss_watch.check)
        reconciler = PositionReconciler(
            book.net_by_instrument, broker, account_id=account.id, clock=clock
        )
        if self.event_log is not None:
            log = self.event_log
            reconciler.subscribe(
                lambda what, m: log.record_note(
                    f"POSITION_MISMATCH_{what.upper()}",
                    account.id,
                    m.message,
                    {"instrument_id": m.instrument_id, "ours": m.ours, "broker": m.broker},
                )
            )
        runtime = AccountRuntime(
            account, broker, orders, book, risk, throttle, switch, reconciler, loss_watch
        )
        self.accounts[account.id] = runtime
        return runtime
