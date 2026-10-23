"""After a restart: rebuild an account's trading state before it may trade again.

    start ─► RecoveryGate: new orders and modifies refused ("recovering"), cancels allowed
          ─► broker reachable? (Kite: after the daily login) ─► recover():
               1. carried positions: from the broker (Kite: overnight qty at the close), or for
                  the paper broker from the audit trail's earlier days, at the last close;
                  paper delivery (CNC) longs become holdings, not positions, as at Kite
               2. today's fills from the audit trail, in order, once each (the book's rule)
               3. today's orders from the audit trail, then the broker's order book over them
               4. a working order the broker doesn't know: the paper broker forgets its orders
                  on a restart, so they are closed, saying so; anywhere else it is a problem
               5. our positions against the broker's: any difference is a problem
          ─► no problem: the gate opens ("ok")
             any problem: the gate stays shut ("failed"), named in words, until fixed and
             recovery is run again; a person must look (nothing is adopted silently)

Why: an empty order manager after a restart looks safe and isn't. It would forget working
orders (and their risk), start today's loss limit from zero, and let algos' leftover
children be cleaned up before the broker had said what happened to them.

Daily P&L keeps its meaning: carried positions start at the previous close (as a broker's
mark-to-market), and only today's fills move today's P&L.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from fractions import Fraction
from zoneinfo import ZoneInfo

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderUpdate, Product
from mocktrade.domain.portfolio import Holding, Trade
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import OrderView
from mocktrade.positions.book import PositionBook
from mocktrade.positions.reconcile import differences
from mocktrade.store.events import EventLog

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
PAPER_GONE = "the paper broker restarted: paper orders don't survive a restart"


def session_start(now: datetime) -> datetime:
    """Midnight in India on `now`'s trading day: what counts as "today"."""
    return datetime.combine(now.astimezone(IST).date(), time(0), IST)


class RecoveryGate:
    """Shut until the account's state has been rebuilt after a restart."""

    def __init__(self) -> None:
        self.state = "recovering"  # "recovering" | "ok" | "failed"
        self.reason = "rebuilding orders and positions after a restart"
        self.finished = asyncio.Event()
        self._listeners: list[Callable[[str, str], None]] = []

    def subscribe(self, listener: Callable[[str, str], None]) -> None:
        self._listeners.append(listener)

    def set(self, state: str, reason: str = "") -> None:
        self.state, self.reason = state, reason
        if state != "recovering":
            self.finished.set()
        for listener in list(self._listeners):
            try:
                listener(state, reason)
            except Exception:
                log.exception("recovery listener failed")

    def _why(self) -> str | None:
        if self.state == "ok":
            return None
        return f"trading is paused after a restart: {self.reason}"

    async def admit_new(self, request: OrderRequest) -> str | None:
        return self._why()

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        return self._why()

    async def admit_cancel(self, order: OrderView) -> str | None:
        return None  # cancelling only ever reduces risk


@dataclass
class RecoveryReport:
    orders: int = 0  # restored from the audit trail
    fills: int = 0  # replayed
    carried: int = 0  # positions carried in from earlier days
    holdings: int = 0  # paper: delivery holdings from earlier days (not positions, as at Kite)
    settled: int = 0  # paper: positions in contracts that expired since (#290)
    closed: list[str] = field(default_factory=list)  # paper orders closed (forgotten by it)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        done = f"{self.orders} orders, {self.fills} fills, {self.carried} carried positions"
        if self.holdings:
            done += f", {self.holdings} holdings"
        if self.settled:
            done += f", {self.settled} expired positions settled"
        if self.closed:
            done += f"; {len(self.closed)} paper orders closed ({PAPER_GONE})"
        return done if self.ok else f"{'; '.join(self.problems)} ({done})"


async def recover(
    account_id: str,
    orders,  # OrderManager
    book: PositionBook,
    broker: Broker,
    log_: EventLog,
    *,
    since: datetime,
    close: Callable[[InstrumentId], int | None] = lambda _i: None,
    latest: Callable[[InstrumentId], Tick | None] = lambda _i: None,
    expired: Callable[[InstrumentId], bool] = lambda _i: False,
    settlement: Callable[[InstrumentId], int | None] = lambda _i: None,
) -> RecoveryReport:
    """Rebuild one account (see the module notes). Raises BrokerError if the broker can't be
    asked yet: nothing has been changed then, so it can simply be tried again.
    `expired`: did this contract expire before today? Paper positions in it are settled at
    `settlement` (its final settlement price; None: unknown, so at its average: no P&L)."""
    broker_positions = await broker.positions()
    broker_orders = await broker.orders()
    durable = getattr(broker, "durable", True)
    report = RecoveryReport()
    carried: dict[tuple[InstrumentId, Product], tuple[int, int]] = {}
    holdings: list[Holding] = []
    trades: list[Trade] = []
    earlier_pnl = Fraction(0)
    fills = log_.fills(account_id)
    today = [e for e in fills if e.ts >= since]

    # 1. carried positions, valued at the previous close
    if durable:
        for p in broker_positions:
            if p.overnight_qty and p.product is not None:
                book.seed(
                    p.instrument_id,
                    p.product,
                    p.overnight_qty,
                    p.close_paise or p.avg_price_paise,
                    carried_avg=p.avg_price_paise,
                )
                report.carried += 1
    else:  # the paper broker: from the audit trail's earlier days
        earlier = PositionBook(account_id)
        for e in fills:
            if e.ts < since and e.order is not None and e.fill is not None:
                earlier.replay(e.client_order_id, e.order.request, e.order.filled_qty,
                               e.fill.qty, e.fill.price_paise)  # fmt: skip
        for e in log_.notes(account_id, ["EXPIRY_SETTLED"]):  # settled at a day's roll
            if e.ts < since:
                x = e.extra
                earlier.settle(x["instrument_id"], Product(x["product"]), int(x["price_paise"]))
        for p in earlier.positions():  # expired while the server was down: settle them now
            if p.qty and expired(p.instrument_id):
                price = settlement(p.instrument_id)
                earlier.settle(
                    p.instrument_id, p.product, round(p.avg_price_paise) if price is None else price
                )
                report.settled += 1
        earlier_pnl = Fraction(earlier.realised_paise())
        for p in earlier.positions():
            if not p.qty:
                continue
            if p.product is Product.CNC and p.qty > 0:  # delivery: a holding from tomorrow on
                holdings.append(Holding(p.instrument_id, p.qty, round(p.avg_price_paise),
                                        close_paise=close(p.instrument_id)))  # fmt: skip
                report.holdings += 1
                continue
            price = close(p.instrument_id) or round(p.avg_price_paise)
            book.seed(p.instrument_id, p.product, p.qty, price, carried_avg=p.avg_price_paise)
            carried[(p.instrument_id, p.product)] = (p.qty, price)
            earlier_pnl += (price - p.avg_price_paise) * p.qty  # settled at the close
            report.carried += 1

    # 2. today's fills, in order, once each
    applied: dict[str, int] = {}
    for e in today:
        if e.order is not None and e.fill is not None:
            book.replay(e.client_order_id, e.order.request, e.order.filled_qty,
                        e.fill.qty, e.fill.price_paise)  # fmt: skip
            report.fills += 1
            qty = e.order.filled_qty - applied.get(e.client_order_id, 0)
            if qty > 0:  # the same rule as the book: each part of a fill once
                applied[e.client_order_id] = e.order.filled_qty
                r = e.order.request
                trades.append(Trade(f"A{e.seq}", e.order.broker_order_id or "", r.instrument_id,
                                    r.side, r.product, qty, e.fill.price_paise, e.ts,
                                    e.client_order_id))  # fmt: skip

    # 3. today's orders, then the broker's word on them
    report.orders = orders.restore(log_.latest_views(account_id, since=since))
    orders.reconcile(broker_orders)  # one list proves nothing about an unanswered order (#259):
    orders.follow_unconfirmed()  # the manager asks the broker again, by itself

    # 4. working orders the broker doesn't know
    listed = {u.broker_order_id for u in broker_orders} | {u.client_order_id for u in broker_orders}
    for view in orders.working():
        if view.broker_order_id is None or view.broker_order_id in listed:
            continue
        if view.client_order_id in listed:
            continue
        if not durable:
            orders.apply(OrderUpdate(view.client_order_id, view.broker_order_id,
                                     OrderStatus.CANCELLED, view.filled_qty, view.avg_price_paise,
                                     reason=PAPER_GONE))  # fmt: skip
            report.closed.append(view.client_order_id)
        else:
            report.problems.append(
                f"order {view.client_order_id} ({view.request.side.value} {view.leaves_qty} "
                f"{view.request.instrument_id}) was working but the broker doesn't list it"
            )

    # valued at the latest prices now, not at the next tick: the loss limit reads this
    for instrument_id in {p.instrument_id for p in book.positions()}:
        if (tick := latest(instrument_id)) is not None:
            book.mark(tick)

    # 5. positions: ours against the broker's
    if not durable and hasattr(broker, "adopt"):
        broker.adopt(carried=carried, trades=trades, holdings=holdings,
                     earlier_pnl_paise=round(earlier_pnl))  # fmt: skip
        broker_positions = await broker.positions()
    # per instrument and product, as the broker lists them (#258: never one row over another)
    different = differences(book.qty_by_product(), broker_positions)
    for (instrument_id, product), (a, b) in sorted(
        different.items(), key=lambda kv: (kv[0][0], kv[0][1].value if kv[0][1] else "")
    ):
        what = f"{instrument_id} {product.value}" if product else instrument_id
        report.problems.append(f"{what}: MockTrade rebuilt {a:+d}, the broker shows {b:+d}")
    return report


async def recover_until_done(
    gate: RecoveryGate,
    attempt: Callable[[], "asyncio.Future[RecoveryReport] | object"],
    *,
    retry_s: float = 10.0,
    audit: Callable[[str, str], None] | None = None,
) -> RecoveryReport:
    """Try recovery until the broker answers (Kite: after the daily login), then open or
    shut the gate for good. Returns the report."""
    while True:
        try:
            report = await attempt()  # type: ignore[misc]
        except BrokerError as exc:
            gate.set(
                "recovering",
                f"waiting for the broker to confirm orders and positions ({exc.reason})",
            )
            await asyncio.sleep(retry_s)
            continue
        except Exception as exc:  # a bug must not leave trading open
            log.exception("recovery failed")
            report = RecoveryReport(problems=[f"recovery could not run: {exc!r}"])
        if report.ok:
            gate.set("ok", report.summary())
        else:
            gate.set("failed", report.summary())
        if audit is not None:
            audit("RECOVERY_OK" if report.ok else "RECOVERY_FAILED", report.summary())
        return report
