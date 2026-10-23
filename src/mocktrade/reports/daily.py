"""The end-of-day report for one account, built from the audit trail (the source of truth).

    audit trail ─► fills before the day ─► positions carried in (valued at the previous close)
                ─► the day's fills, in order, once each ─► positions, realised P&L
                ─► marks (the last prices) ─► unrealised P&L
                ─► the day's executed orders ─► charges (charges/model.py) ─► net
                ─► rejects and alerts of the day

P&L follows the same convention as the live Positions screen and brokers' day P&L: what was
carried in starts at the previous close, so a day's P&L is that day's move only, and adding
up the days gives the P&L over the period. Without prices (a past day rebuilt later), carried
positions start at their cost and open positions have no unrealised P&L: the report says so
(`priced` is False) instead of guessing.

P&L by strategy: each fill belongs to its order's basket ("basket B7"), its algo ("algo A3"),
or "manual"; what was carried in is "carried". Each strategy keeps its own book, so its gross
P&L is its own trades' P&L; the strategies add up to the account's gross P&L exactly
(P&L = sells − buys + open qty × mark is additive).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mocktrade.charges.model import ChargesModel, executed, position_key
from mocktrade.domain.instruments import Instrument, InstrumentId
from mocktrade.domain.orders import OrderRequest
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import EventKind
from mocktrade.positions.book import PositionBook
from mocktrade.store.events import EventLog

IST = ZoneInfo("Asia/Kolkata")
CARRIED = "carried"
MANUAL = "manual"
# audit notes that belong in a day's report as alerts (prefixes)
ALERTS = ("POSITION_MISMATCH", "RECOVERY_FAILED", "ALGO_LOST", "ALGO_FAILED", "KILL_SWITCH",
          "GTT_REJECTED")  # fmt: skip


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Midnight to midnight in India."""
    start = datetime.combine(day, time(0), IST)
    return start, start + timedelta(days=1)


def strategy_of(request: OrderRequest) -> str:
    if request.basket_id and request.basket_id.startswith(("SQ", "MTM")):
        return "square-off" if request.basket_id.startswith("SQ") else "MTM stop"
    if request.basket_id:
        return f"basket {request.basket_id}"
    if request.parent_id:
        return f"algo {request.parent_id}"
    return MANUAL


@dataclass(frozen=True)
class TradeRow:
    ts: datetime
    client_order_id: str
    instrument_id: InstrumentId
    side: str
    product: str
    qty: int
    price_paise: int
    strategy: str


@dataclass(frozen=True)
class PositionRow:
    instrument_id: InstrumentId
    product: str
    carried_qty: int  # held at the start of the day
    reference_paise: int | None  # what carried qty is valued from (previous close, else cost)
    buy_qty: int
    buy_value_paise: int
    sell_qty: int
    sell_value_paise: int
    closing_qty: int  # carried forward to the next day
    mark_paise: int | None  # the last price used for unrealised P&L
    realised_paise: int
    unrealised_paise: int
    charges_paise: int

    @property
    def gross_paise(self) -> int:
        return self.realised_paise + self.unrealised_paise

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.charges_paise


@dataclass(frozen=True)
class StrategyRow:
    strategy: str
    trades: int
    gross_paise: int
    charges_paise: int

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.charges_paise


@dataclass(frozen=True)
class Note:
    ts: datetime
    kind: str
    detail: str


@dataclass(frozen=True)
class DayReport:
    account_id: str
    day: date
    generated_at: datetime
    priced: bool  # every open position had a last price (otherwise unrealised is partial)
    final: bool  # built after the close (kept), not a live preview during the day
    trades: tuple[TradeRow, ...]
    positions: tuple[PositionRow, ...]
    strategies: tuple[StrategyRow, ...]
    charge_lines: tuple[tuple[str, int], ...]
    turnover_paise: int
    rejects: tuple[Note, ...]
    alerts: tuple[Note, ...]
    schedule: str = ""  # the charge schedule used, with the date its rates were checked
    notes: tuple[str, ...] = field(default=())  # what the reader should know (no prices…)

    @property
    def realised_paise(self) -> int:
        return sum(p.realised_paise for p in self.positions)

    @property
    def unrealised_paise(self) -> int:
        return sum(p.unrealised_paise for p in self.positions)

    @property
    def gross_paise(self) -> int:
        return self.realised_paise + self.unrealised_paise

    @property
    def charges_paise(self) -> int:
        return sum(paise for _, paise in self.charge_lines)

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.charges_paise

    @property
    def carried_forward(self) -> tuple[PositionRow, ...]:
        return tuple(p for p in self.positions if p.closing_qty)


def build_day(
    account_id: str,
    day: date,
    log: EventLog,
    instruments: Callable[[str], Instrument | None],
    charges: ChargesModel,
    *,
    prices: Callable[[str], Tick | None] | None = None,
    now: datetime,
    final: bool = False,
) -> DayReport:
    """`prices`: the market's latest ticks (their close = the previous close, their ltp = the
    mark). None for a past day rebuilt without prices."""
    start, end = day_bounds(day)
    fills = [e for e in log.fills(account_id) if e.ts < end and e.order and e.fill]
    before = [e for e in fills if e.ts < start]
    today = [e for e in fills if e.ts >= start]

    # carried in: from earlier days' fills, valued at the previous close (else at cost)
    earlier = PositionBook(account_id)
    for e in before:
        earlier.replay(e.client_order_id, e.order.request, e.order.filled_qty,  # type: ignore[union-attr]
                       e.fill.qty, e.fill.price_paise, e.fill.value_paise)  # type: ignore[union-attr]  # fmt: skip
    book = PositionBook(account_id)
    books: dict[str, PositionBook] = {}
    carried: dict[tuple[str, str], tuple[int, int]] = {}
    notes: list[str] = []
    unpriced_carry = False
    for p in earlier.positions():
        if not p.qty:
            continue
        tick = prices(p.instrument_id) if prices else None
        reference = tick.close_paise if tick and tick.close_paise else None
        if reference is None:
            unpriced_carry = True
            reference = round(p.avg_price_paise)
        book.seed(p.instrument_id, p.product, p.qty, reference)
        books.setdefault(CARRIED, PositionBook(account_id)).seed(
            p.instrument_id, p.product, p.qty, reference
        )
        carried[(p.instrument_id, p.product.value)] = (p.qty, reference)
    if unpriced_carry:
        notes.append("no previous close for some carried positions: valued at their cost")

    # the day's fills, each once
    trades: list[TradeRow] = []
    counted: dict[str, int] = {}
    per_strategy_trades: dict[str, int] = {}
    for e in today:
        order, fill = e.order, e.fill
        assert order is not None and fill is not None
        r = order.request
        strategy = strategy_of(r)
        if fill.qty == 0:  # the broker corrected what this order traded for (#275)
            for b in (book, books.setdefault(strategy, PositionBook(account_id))):
                b.replay(e.client_order_id, r, order.filled_qty, 0, 0, fill.value_paise)
            continue
        qty = order.filled_qty - counted.get(e.client_order_id, 0)
        if qty <= 0:
            continue
        counted[e.client_order_id] = order.filled_qty
        value = fill.value_paise if qty == fill.qty else None
        book.replay(e.client_order_id, r, order.filled_qty, fill.qty, fill.price_paise, value)
        books.setdefault(strategy, PositionBook(account_id)).replay(
            e.client_order_id, r, order.filled_qty, fill.qty, fill.price_paise, value
        )
        per_strategy_trades[strategy] = per_strategy_trades.get(strategy, 0) + 1
        trades.append(TradeRow(e.ts, e.client_order_id, r.instrument_id, r.side.value,
                               r.product.value, qty, fill.price_paise, strategy))  # fmt: skip

    # marks
    priced = True
    marks: dict[str, int] = {}
    for p in book.positions():
        tick = prices(p.instrument_id) if prices else None
        if tick is not None and tick.ltp_paise > 0:
            marks[p.instrument_id] = tick.ltp_paise
        elif p.qty:
            priced = False
    for b in (book, *books.values()):
        for instrument_id, ltp in marks.items():
            b.mark(Tick(instrument_id, ltp))
    if not priced:
        notes.append("no last price for some open positions: their unrealised P&L counts as 0")

    # charges: the day's executed orders (orders created on the day)
    views = [v for v in log.latest_views(account_id, since=start) if v.created_at < end]
    bill = charges.bill(executed(views, instruments))
    strategy_charges: dict[str, int] = {}
    for strategy in books:
        if strategy == CARRIED:
            continue
        mine = [v for v in views if strategy_of(v.request) == strategy]
        strategy_charges[strategy] = charges.bill(executed(mine, instruments)).total_paise

    positions = []
    for p in book.positions():
        qty_in, reference = carried.get((p.instrument_id, p.product.value), (0, None))
        positions.append(
            PositionRow(
                instrument_id=p.instrument_id,
                product=p.product.value,
                carried_qty=qty_in,
                reference_paise=reference,
                buy_qty=p.buy_qty,
                buy_value_paise=p.buy_value_paise,
                sell_qty=p.sell_qty,
                sell_value_paise=p.sell_value_paise,
                closing_qty=p.qty,
                mark_paise=marks.get(p.instrument_id),
                realised_paise=p.realised_paise,
                unrealised_paise=p.unrealised_paise,
                charges_paise=bill.by_position.get(position_key(p.instrument_id, p.product), 0),
            )
        )
    strategies = tuple(
        StrategyRow(name, per_strategy_trades.get(name, 0),
                    b.realised_paise() + b.unrealised_paise(), strategy_charges.get(name, 0))
        for name, b in sorted(books.items(), key=lambda kv: (kv[0] != CARRIED, kv[0]))
    )  # fmt: skip

    rejects, alerts = _notes(log, account_id, start, end)
    return DayReport(
        account_id=account_id,
        day=day,
        generated_at=now,
        priced=priced and not unpriced_carry,
        final=final,
        trades=tuple(trades),
        positions=tuple(positions),
        strategies=strategies,
        charge_lines=tuple((line.label, line.paise) for line in bill.lines),
        turnover_paise=bill.turnover_paise,
        rejects=rejects,
        alerts=alerts,
        schedule=f"{charges.schedule.name} (rates checked {charges.schedule.checked_on})",
        notes=tuple(notes),
    )


def _notes(
    log: EventLog, account_id: str, start: datetime, end: datetime
) -> tuple[tuple[Note, ...], tuple[Note, ...]]:
    rejects, alerts = [], []
    for e in log.between([account_id, f"account {account_id}"], start, end):
        if e.kind == EventKind.REJECTED.value and e.order is not None:
            r = e.order.request
            reason = e.order.reason or e.detail
            rejects.append(Note(e.ts, "REJECTED", f"{e.client_order_id} {r.side.value} {r.qty} "
                                                  f"{r.instrument_id}: {reason}"))  # fmt: skip
        elif e.kind.startswith(ALERTS):
            alerts.append(Note(e.ts, e.kind, e.detail))
    return tuple(rejects), tuple(alerts)


# --- what the API sends, what is kept ------------------------------------------------------------


def report_to_dict(r: DayReport) -> dict[str, Any]:
    return {
        "account_id": r.account_id,
        "day": r.day.isoformat(),
        "generated_at": r.generated_at.isoformat(),
        "final": r.final,
        "priced": r.priced,
        "notes": list(r.notes),
        "schedule": r.schedule,
        "totals": {
            "realised_paise": r.realised_paise,
            "unrealised_paise": r.unrealised_paise,
            "gross_paise": r.gross_paise,
            "charges_paise": r.charges_paise,
            "net_paise": r.net_paise,
            "turnover_paise": r.turnover_paise,
            "trades": len(r.trades),
        },
        "charge_lines": [{"label": label, "paise": paise} for label, paise in r.charge_lines],
        "positions": [
            {
                "instrument_id": p.instrument_id,
                "product": p.product,
                "carried_qty": p.carried_qty,
                "reference_paise": p.reference_paise,
                "buy_qty": p.buy_qty,
                "buy_value_paise": p.buy_value_paise,
                "sell_qty": p.sell_qty,
                "sell_value_paise": p.sell_value_paise,
                "closing_qty": p.closing_qty,
                "mark_paise": p.mark_paise,
                "realised_paise": p.realised_paise,
                "unrealised_paise": p.unrealised_paise,
                "gross_paise": p.gross_paise,
                "charges_paise": p.charges_paise,
                "net_paise": p.net_paise,
            }
            for p in r.positions
        ],
        "strategies": [
            {
                "strategy": s.strategy,
                "trades": s.trades,
                "gross_paise": s.gross_paise,
                "charges_paise": s.charges_paise,
                "net_paise": s.net_paise,
            }
            for s in r.strategies
        ],  # fmt: skip
        "trades": [
            {
                "ts": t.ts.isoformat(),
                "client_order_id": t.client_order_id,
                "instrument_id": t.instrument_id,
                "side": t.side,
                "product": t.product,
                "qty": t.qty,
                "price_paise": t.price_paise,
                "value_paise": t.qty * t.price_paise,
                "strategy": t.strategy,
            }
            for t in r.trades
        ],  # fmt: skip
        "rejects": [{"ts": n.ts.isoformat(), "detail": n.detail} for n in r.rejects],
        "alerts": [{"ts": n.ts.isoformat(), "kind": n.kind, "detail": n.detail} for n in r.alerts],
    }
