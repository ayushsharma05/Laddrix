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
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NamedTuple

from mocktrade.algos.engine import AlgoEngine, AlgoEvent, AlgoType
from mocktrade.broker.base import Broker, GttBook
from mocktrade.broker.rules import order_spec
from mocktrade.charges.model import ChargesModel, ScheduleCharges
from mocktrade.charges.schedules import KITE_STANDARD
from mocktrade.charges.service import ChargesService
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.money import format_rupees
from mocktrade.domain.order_spec import OrderSpec
from mocktrade.domain.orders import Product
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.basket import Baskets
from mocktrade.orders.gate import Gates
from mocktrade.orders.gtt import GttService
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.portfolio.service import PortfolioService
from mocktrade.positions.book import PositionBook
from mocktrade.positions.reconcile import PositionReconciler
from mocktrade.recovery import IST, RecoveryGate, recover, recover_until_done, session_start
from mocktrade.refdata.expiry import Expiries, ExpiryRules
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.checks import RiskGate
from mocktrade.risk.closing import ExitDesk, position_of
from mocktrade.risk.flatten import Flatten
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.risk.loss_watch import DailyLossWatch
from mocktrade.risk.margin import MarginGate
from mocktrade.risk.mtm import MtmWatch
from mocktrade.risk.squareoff import SquareOff
from mocktrade.risk.throttle import OrderThrottle
from mocktrade.store.events import EventLog


def unknown_size(reconciler: PositionReconciler) -> Callable[[str, Product], str | None]:
    """Why a position's size isn't known (#262), or None: the broker and MockTrade disagree
    on it (a reconcile alert), or a difference is being re-checked."""

    def unknown(instrument_id: str, product: Product) -> str | None:
        for m in reconciler.alerts():
            if m.instrument_id == instrument_id and m.product in (None, product):
                return m.message
        last = reconciler.last
        for i, p in last.suspected if last is not None else ():
            if i == instrument_id and p in (None, product):
                return "the broker's position differs and is being re-checked"
        return None

    return unknown


class LostProtection(NamedTuple):
    """A protection algo that was running when the server stopped (it doesn't survive one)."""

    instrument_id: str
    words: str  # "TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT (A1, ayush)"
    algo: dict | None = None  # what it was (code, side, qty, params, product…), to restore it


ALGO_ENDS = ("DONE", "STOPPED", "FAILED", "LOST")  # audit kinds ALGO_<end>: no longer running


log = logging.getLogger(__name__)


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
    algos: AlgoEngine
    baskets: Baskets
    recovery: RecoveryGate | None = None  # set when the account rebuilds its state at start
    gtt: GttService | None = None  # when the broker holds GTT orders
    portfolio: PortfolioService | None = None  # funds, holdings, trades (what the broker offers)
    charges: ChargesService | None = None  # the day's charges, and estimates for the ticket
    margin: MarginGate | None = None  # quotes an order's margin (the ticket)...
    margin_check: bool = False  # ...and, when on, refuses what the funds can't carry
    mtm: MtmWatch | None = None  # the trader's own loss limits (per position, portfolio)
    squareoff: SquareOff | None = None  # intraday positions before the broker's cut-off
    flatten: Flatten | None = None  # closing positions on purpose, STOP or not (#262)
    session_day: date | None = None  # the trading day its positions and limits are for (#266)

    def apply_limits(self, limits: RiskLimits) -> None:
        """An admin's new limits, from the next order on. Everything that copied a limit at
        the start reads the new one: the checks, the daily-loss stop and the closing pieces
        (MTM, square-off). Working orders are left as they are."""
        self.risk.limits = limits
        if self.loss_watch is not None:
            self.loss_watch.limit_paise = limits.max_daily_loss_paise
        for closer in (self.mtm, self.squareoff, self.flatten):
            if closer is not None:
                closer.max_lots = limits.max_order_lots

    def set_active(self, active: bool) -> None:
        """Turned off by an admin: only closing trades from then on (cancels always work)."""
        self.risk.off_reason = None if active else "the account is turned off by an admin"


@dataclass
class Platform:
    instruments: InstrumentStore
    market_data: MarketData
    firm_switch: KillSwitch
    event_log: EventLog | None = None
    accounts: dict[str, AccountRuntime] = field(default_factory=dict)
    charges_model: ChargesModel = field(default_factory=lambda: ScheduleCharges(KITE_STANDARD))
    # every order on disk before the broker sees it (#265); off only for backtests, which
    # replay a day faster than real time and never send a real order
    durable_orders: bool = True
    expiry_rules: ExpiryRules = field(default_factory=ExpiryRules)  # broker cut-offs (#290)
    expiries: Expiries = field(init=False, repr=False)
    _late: set[asyncio.Task] | None = field(default=None, init=False, repr=False)
    _reconcile: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        self.expiries = Expiries(self.instruments, rules=self.expiry_rules)
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
        position checks, and marking positions to the latest prices. An account added while
        this runs (an admin's new account) starts its own loops at once, ended with these."""
        self._reconcile = reconcile
        loops = [self._mark_loop()]
        for runtime in self.accounts.values():
            loops.extend(self._account_loops(runtime))
        self._late = set()
        try:
            await asyncio.gather(*loops)
        finally:
            late, self._late = self._late, None
            for task in late:
                task.cancel()
            if late:
                await asyncio.gather(*late, return_exceptions=True)

    def _account_loops(self, runtime: AccountRuntime) -> list:
        loops = [runtime.orders.run(), self._run_algos(runtime)]
        if runtime.recovery is not None:
            loops.append(self._recover(runtime))
        if runtime.gtt is not None:
            loops.append(self._after_recovery(runtime, runtime.gtt.run))
        if runtime.portfolio is not None:
            loops.append(self._after_recovery(runtime, runtime.portfolio.run))
        if self._reconcile:
            loops.append(self._after_recovery(runtime, runtime.reconciler.run))
        return loops

    def _start_late(self, runtime: AccountRuntime) -> None:
        """An account added after run() started: its loops as tasks run() ends with its own."""
        if self._late is None:
            return  # not running yet: run() starts it with the others
        for loop in self._account_loops(runtime):
            task = asyncio.ensure_future(loop)
            self._late.add(task)
            task.add_done_callback(self._late_done)

    def _late_done(self, task: asyncio.Task) -> None:
        if self._late is not None:
            self._late.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("a loop of a late-added account failed", exc_info=task.exception())

    async def _recover(self, runtime: AccountRuntime, retry_s: float = 10.0) -> None:
        """Rebuild the account from the audit trail and the broker (recovery.py)."""
        assert runtime.recovery is not None and self.event_log is not None
        log, account_id = self.event_log, runtime.account.id

        def close(instrument_id: str) -> int | None:
            tick = self.market_data.latest(instrument_id)
            return tick.close_paise if tick is not None else None

        def expired(instrument_id: str) -> bool:  # before today (#290)
            instrument = self.instruments.get(instrument_id)
            today = runtime.positions._clock().astimezone(IST).date()
            return instrument is not None and self.expiries.expired_by(today)(instrument)

        def settlement(instrument_id: str) -> int | None:
            instrument = self.instruments.get(instrument_id)
            found = instrument and self.expiries.settlement_price(instrument, self.last_price)
            return found if found is not None else close(instrument_id)

        def attempt():
            return recover(
                account_id,
                runtime.orders,
                runtime.positions,
                runtime.broker,
                log,
                since=session_start(runtime.positions._clock()),  # the account's clock
                close=close,
                latest=self.market_data.latest,
                expired=expired,
                settlement=settlement,
            )

        await recover_until_done(
            runtime.recovery,
            attempt,
            retry_s=retry_s,
            audit=lambda kind, detail: log.record_note(kind, account_id, detail),
        )

    @staticmethod
    async def _after_recovery(runtime: AccountRuntime, loop) -> None:
        """Position checks start once the state is rebuilt (an empty book would mismatch)."""
        if runtime.recovery is not None:
            await runtime.recovery.finished.wait()
        await loop()

    def unknown_size_of(self, account_id: str, instrument_id: str, product) -> str | None:
        """Why this position's size isn't known (the broker disagrees), or None."""
        runtime = self.accounts.get(account_id)
        return None if runtime is None else unknown_size(runtime.reconciler)(instrument_id, product)

    def roll_day(self, now: datetime | None = None) -> dict[str, str]:
        """A new trading day without a restart (#266), for every account not yet on it:
        yesterday's day orders end as expired, overnight positions carry over at the last
        price with today's P&L at zero (so the daily loss limit and MTM limits start fresh),
        and the charges are worked out again. Once per account per day: running it twice, or
        after a restart (which builds today from scratch), changes nothing. Returns what it
        did per account, in words."""
        done = {}
        for account_id, runtime in self.accounts.items():
            at = now or runtime.positions._clock()
            today = at.astimezone(IST).date()
            if runtime.session_day is not None and runtime.session_day >= today:
                continue
            expired = runtime.orders.expire_day(session_start(at))
            settled = self._settle_expired(account_id, runtime, today)
            carried = runtime.positions.roll()
            if runtime.charges is not None:
                runtime.charges.refresh()
            runtime.session_day = today
            words = (f"new trading day {today.isoformat()}: {len(carried)} position(s) "
                     f"carried at the last price, {len(expired)} day order(s) expired, "
                     f"{len(settled)} expired contract(s) settled")  # fmt: skip
            done[account_id] = words
            log.info("%s: %s", account_id, words)
            if self.event_log is not None:
                extra = {"day": today.isoformat(), "expired": expired}
                self.event_log.record_note("SESSION_ROLLED", account_id, words, extra)
        return done

    def last_price(self, instrument_id: str) -> int | None:
        tick = self.market_data.latest(instrument_id)
        return tick.ltp_paise if tick is not None and tick.ltp_paise > 0 else None

    def _settle_expired(self, account_id: str, runtime: AccountRuntime, today: date) -> list[str]:
        """Positions in contracts that expired before `today` close at their final settlement
        price (#290), in the platform's book and (paper) at the broker, before the day rolls:
        the result belongs to the expiry day. A live broker settles them itself; reconciling
        then finds both sides flat. Each is noted in the audit trail, which recovery reads."""
        ended = self.expiries.expired_by(today)
        done = []
        for p in runtime.positions.positions():
            instrument = self.instruments.get(p.instrument_id)
            if not p.qty or instrument is None or not ended(instrument):
                continue
            price = self.expiries.settlement_price(instrument, self.last_price)
            if price is None:
                price = p.ltp_paise or round(p.avg_price_paise)
            qty = runtime.positions.settle(p.instrument_id, p.product, price)
            settle_at_broker = getattr(runtime.broker, "settle_expired", None)
            if callable(settle_at_broker):
                settle_at_broker(p.instrument_id, p.product, price)
            how = "cash" if not self.expiries.physical(instrument) else "physical"
            words = (f"{p.instrument_id} {p.product.value} {qty:+d} expired: settled at "
                     f"{format_rupees(price)} ({how} settlement)")  # fmt: skip
            done.append(words)
            log.info("%s: %s", account_id, words)
            if self.event_log is not None:
                extra = {"instrument_id": p.instrument_id, "product": p.product.value,
                         "qty": qty, "price_paise": price, "settlement": how}  # fmt: skip
                self.event_log.record_note("EXPIRY_SETTLED", account_id, words, extra)
        return done

    def lost_protection(self, account_id: str) -> list[LostProtection]:
        """At start: protection algos (stops held by MockTrade) that were running when the
        server last stopped. They don't survive a restart, so each is named, in words, for
        an alert; and noted as lost in the audit, so it is reported once."""
        if self.event_log is None:
            return []
        log = self.event_log
        open_: dict[str, dict] = {}
        for e in log.notes(account_id, [f"ALGO_{k}" for k in ALGO_ENDS + ("STARTED",)]):
            algo_id = e.extra.get("algo_id", "")
            if e.kind == "ALGO_STARTED":
                if e.extra.get("family") == "protection":
                    open_[algo_id] = e.extra
            else:
                open_.pop(algo_id, None)
        lost = []
        for algo_id, a in open_.items():
            words = (
                f"{a.get('code')} {a.get('side')} {a.get('qty')} {a.get('instrument_id')} "
                f"({algo_id}, {a.get('user_id')})"
            )
            record = {k: a.get(k) for k in ("code", "side", "qty", "params", "product", "user_id")}
            lost.append(LostProtection(str(a.get("instrument_id", "")), words,
                                       {"algo_id": algo_id, **record}))  # fmt: skip
            log.record_note("ALGO_LOST", account_id, f"not running after a restart: {words}", a)
        return lost

    @staticmethod
    async def _run_algos(runtime: AccountRuntime) -> None:
        """Algos don't survive a restart: once the broker has said what happened to every
        order (recovery), cancel their leftover children, then run."""
        if runtime.recovery is not None:
            await runtime.recovery.finished.wait()
        await runtime.algos.cancel_orphans()
        await runtime.algos.run()

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

    def _algo_engine(
        self,
        account_id: str,
        orders: OrderManager,
        switch: KillSwitch,
        types: tuple[AlgoType, ...],
        clock: Callable[[], datetime],
        book: PositionBook | None = None,
    ) -> AlgoEngine:
        """The account's algos: blocked while a kill switch is on, stopped when one engages,
        every state change audited."""

        def blocked() -> str | None:
            for s in (self.firm_switch, switch):
                if s.engaged:
                    return f"trading is stopped: {s.state.reason}"
            return None

        audit = None
        family = {t.spec.code: t.spec.family for t in types}
        if self.event_log is not None:
            log = self.event_log

            def audit(event: AlgoEvent) -> None:
                a = event.algo
                log.record_note(
                    f"ALGO_{event.kind}",
                    account_id,
                    event.detail,
                    {
                        "algo_id": a.algo_id,
                        "code": a.code,
                        "family": family.get(a.code, ""),
                        "instrument_id": a.instrument_id,
                        "side": a.side.value,
                        "qty": a.qty,
                        "filled_qty": a.filled_qty,
                        "params": a.params,
                        "user_id": a.user_id,
                        "product": a.product.value,
                    },
                    ts=event.ts,
                )

        engine = AlgoEngine(
            account_id,
            orders,
            self.market_data,
            self.instruments.get,
            types,
            blocked=blocked,
            clock=clock,
            audit=audit,
            position=None if book is None else position_of(book),
        )

        def on_switch(_name: str, state) -> None:
            if state.engaged:
                engine.stop_all(f"trading stopped: {state.reason}")

        switch.on_change(on_switch)
        self.firm_switch.on_change(on_switch)
        return engine

    def add_account(
        self,
        account: BrokerAccount,
        broker: Broker,
        *,
        limits: RiskLimits | None = None,
        ids: ClientIds,
        auto_kill_on_loss: bool = False,
        throttle: OrderThrottle | None = None,
        algo_types: tuple[AlgoType, ...] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        recover_on_start: bool = False,
        margin_check: bool = False,
        rules_dir: Path | None = None,
    ) -> AccountRuntime:
        """`recover_on_start`: the server's setting. The account takes no new orders until
        its orders and positions are rebuilt from the audit trail and checked against the
        broker (recovery.py). Needs the event log.
        `margin_check`: refuse orders the funds can't carry (risk/margin.py; fails open).
        `rules_dir`: where the trader's MTM limits are kept (risk/mtm.py; None: memory)."""
        if recover_on_start and self.event_log is None:
            raise ValueError("recovery after a restart needs the event log")
        if account.id in self.accounts:
            raise ValueError(f"account {account.id} is already running")
        limits = limits or RiskLimits()
        switch = KillSwitch(f"account {account.id}", clock=clock)
        book = PositionBook(account.id, clock=clock)
        holder: dict[str, Any] = {}  # the gates need what they guard (orders, portfolio)
        risk = RiskGate(
            limits,
            instruments=self.instruments.get,
            prices=self.market_data.latest,
            position=book.net_qty,
            working=lambda: holder["orders"].working(),
            day_pnl_paise=book.day_pnl_paise,
            switches=(self.firm_switch, switch),
            clock=clock,
            positions=book.net_by_instrument,
            product_position=book.qty,
            price_age=self.market_data.age_s,  # per instrument: freshness (#267)
            expiry=self.expiries.info,  # expired contracts, expiry-day cut-offs (#290)
        )
        throttle = throttle or OrderThrottle()
        recovery = RecoveryGate() if recover_on_start else None

        # the margin quote exists for every account; the gate only when margin_check is on
        async def available() -> int | None:
            portfolio = holder.get("portfolio")
            if portfolio is None or not portfolio.offers("funds"):
                return None
            funds = (await portfolio.read("funds")).items
            equity = [f for f in funds if f.segment == "equity"] or list(funds)
            return equity[0].available_paise if equity else None

        def unchecked(request, why: str) -> None:
            if self.event_log is not None:
                self.event_log.record_note(
                    "MARGIN_UNCHECKED", account.id,
                    f"{request.side.value} {request.qty} {request.instrument_id} sent "
                    f"without a margin check: {why}",
                )  # fmt: skip

        margin = MarginGate(broker, available, book.net_qty, on_unchecked=unchecked)
        checks = (recovery, risk, margin if margin_check else None, throttle)
        gates = [g for g in checks if g is not None]
        orders = OrderManager(
            account.id,
            broker,
            self.instruments.get,
            ids=ids,
            gate=Gates(gates),
            spec=spec_of(broker),
            clock=clock,
            durable=(
                self.event_log.persist
                if self.event_log is not None and self.durable_orders
                else None
            ),  # #265
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
            book.qty_by_product, broker, account_id=account.id, clock=clock
        )
        if self.event_log is not None:
            log = self.event_log
            reconciler.subscribe(
                lambda what, m: log.record_note(
                    f"POSITION_MISMATCH_{what.upper()}",
                    account.id,
                    m.message,
                    {
                        "instrument_id": m.instrument_id,
                        "ours": m.ours,
                        "broker": m.broker,
                        "product": m.product.value if m.product else None,
                    },
                )
            )
        algos = self._algo_engine(account.id, orders, switch, algo_types, clock, book)
        basket_audit = None
        if self.event_log is not None:
            log = self.event_log

            def basket_audit(kind: str, detail: str, extra: dict) -> None:
                log.record_note(kind, account.id, detail, extra)

        baskets = Baskets(
            account.id,
            orders,
            risk,
            broker,
            self.instruments.get,
            self.market_data.latest,
            audit=basket_audit,
        )
        gtt = None
        if isinstance(broker, GttBook):
            gtt_audit = None
            if self.event_log is not None:
                log = self.event_log

                def gtt_audit(kind: str, detail: str, extra: dict) -> None:
                    log.record_note(kind, account.id, detail, extra)

            gtt = GttService(
                account.id, broker, orders, risk, self.instruments.get, self.market_data.latest,
                audit=gtt_audit, clock=clock,
            )  # fmt: skip

        def client_id_of(broker_order_id: str) -> str | None:
            view = orders.by_broker_id(broker_order_id)
            return view.client_order_id if view is not None else None

        portfolio = PortfolioService(
            account.id, broker, self.market_data, client_id_of=client_id_of, clock=clock
        )
        orders.subscribe(portfolio.on_order_event)
        holder["portfolio"] = portfolio
        charges = ChargesService(
            account.id, self.charges_model, orders, self.instruments.get, clock=clock
        )
        orders.subscribe(charges.on_order_event)
        note = None
        if self.event_log is not None:
            log = self.event_log

            def note(kind: str, detail: str, extra: dict) -> None:
                log.record_note(kind, account.id, detail, extra)

        desk = ExitDesk(orders, position_of(book), algos=lambda: algos)  # every forced close
        mtm = MtmWatch(
            account.id, book, orders, self.instruments.get, max_lots=limits.max_order_lots,
            path=rules_dir / f"mtm-{account.id}.json" if rules_dir else None,
            audit=note, clock=clock, desk=desk, unknown=unknown_size(reconciler),
            ready=lambda: recovery is None or recovery.state == "ok",
        )  # fmt: skip
        book.subscribe(mtm.check)
        orders.subscribe(mtm.on_order_event)  # a refused close moves no position (#263)
        squareoff = SquareOff(account.id, book, orders, self.instruments.get,
                              max_lots=limits.max_order_lots, audit=note, clock=clock,
                              desk=desk)  # fmt: skip
        firm = self.firm_switch

        def stopped():
            return next((sw.state for sw in (switch, firm) if sw.engaged), None)

        flatten = Flatten(account.id, book, orders, desk, self.instruments.get,
                          max_lots=limits.max_order_lots, unknown=unknown_size(reconciler),
                          stopped=stopped, algos=lambda: algos, audit=note,
                          clock=clock)  # fmt: skip
        runtime = AccountRuntime(
            account,
            broker,
            orders,
            book,
            risk,
            throttle,
            switch,
            reconciler,
            loss_watch,
            algos,
            baskets,
            recovery,
            gtt,
            portfolio,
            charges,
            margin,
            margin_check,
            mtm,
            squareoff,
            flatten,
        )
        self.accounts[account.id] = runtime
        runtime.session_day = clock().astimezone(IST).date()  # a start rebuilds today (recovery)
        runtime.set_active(account.active)
        self._start_late(runtime)
        return runtime


def spec_of(broker: Broker) -> Callable[[Instrument], OrderSpec]:
    """The broker's own order spec if it publishes one, else the standard NSE/BSE one."""
    published = getattr(broker, "order_spec", None)
    return published if callable(published) else order_spec
