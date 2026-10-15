"""What a trade costs beyond the price: brokerage and the taxes and fees on Indian trades.

    executed orders ─► ChargesModel ─► ChargeBill: labelled lines, by instrument, total

    line              charged on                                  who
    Brokerage         each executed order (not each fill)         the broker
    STT               the buy and/or sell value (by segment)      the government
    Exchange charges  the turnover (rate by exchange)             NSE / BSE
    SEBI fee          the turnover (₹10 a crore)                  SEBI
    IPFT              the turnover (NSE investor protection)      NSE
    Stamp duty        the buy value only                          the state
    GST               18% of brokerage + exchange + SEBI + IPFT   the government
    DP charges        per stock sold from the demat, per day      the depository + broker

Rates are data, one table per broker and plan with the date they were checked
(charges/schedules.py), never code: a budget changes STT, an exchange changes its fee, and
only the table changes. Every amount is worked exactly (fractions of a paisa kept) and each
line is rounded to the paisa once per instrument, at the end, as a contract note does.

Segments: equity delivery (CNC), equity intraday (MIS), futures, options. Options are charged
on the premium. Not covered (yet): STT on options exercised at expiry (charged on the
intrinsic value at expiry), currency and commodity segments.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from fractions import Fraction
from typing import Any, Protocol

from mocktrade.domain.instruments import Instrument, InstrumentId, InstrumentKind
from mocktrade.domain.orders import Product, Side

# The exchange that charges the fee: F&O contracts trade on NFO/BFO, segments of NSE/BSE.
VENUE = {"NSE": "NSE", "NFO": "NSE", "BSE": "BSE", "BFO": "BSE"}


def position_key(instrument_id: InstrumentId, product: Product) -> str:
    """The key a position is known by on screens: "NFO:NIFTY26OCTFUT|NRML"."""
    return f"{instrument_id}|{product.value}"


def venue(instrument: Instrument) -> str:
    return VENUE.get(instrument.exchange.value, instrument.exchange.value)


class Segment(Enum):
    DELIVERY = "equity delivery"
    INTRADAY = "equity intraday"
    FUTURES = "futures"
    OPTIONS = "options"


def segment_of(instrument: Instrument, product: Product) -> Segment | None:
    """None for what can't be traded (an index)."""
    kind = instrument.kind
    if kind is InstrumentKind.EQUITY:
        return Segment.DELIVERY if product is Product.CNC else Segment.INTRADAY
    if kind is InstrumentKind.FUTURE:
        return Segment.FUTURES
    if kind in (InstrumentKind.CALL, InstrumentKind.PUT):
        return Segment.OPTIONS
    return None


@dataclass(frozen=True)
class SegmentRates:
    """Percentages are of the traded value (options: the premium)."""

    brokerage_pct: Fraction = Fraction(0)  # of each executed order's value...
    brokerage_cap_paise: int | None = None  # ...but at most this per executed order
    brokerage_flat_paise: int | None = None  # or a flat fee per executed order
    stt_buy_pct: Fraction = Fraction(0)
    stt_sell_pct: Fraction = Fraction(0)
    exchange_pct: dict[str, Fraction] = field(default_factory=dict)  # by exchange code
    stamp_buy_pct: Fraction = Fraction(0)
    ipft_pct: dict[str, Fraction] = field(default_factory=dict)  # by exchange code
    dp_paise: int = 0  # per stock sold out of the demat, per day (GST included)


@dataclass(frozen=True)
class ChargeSchedule:
    name: str  # the broker and plan, e.g. "Kite standard"
    checked_on: date  # when these rates were last compared with the published ones
    source: str  # where to check them again
    segments: dict[Segment, SegmentRates]
    sebi_pct: Fraction = Fraction(1, 10000)  # ₹10 a crore
    gst_pct: Fraction = Fraction(18)


@dataclass(frozen=True)
class ExecutedOrder:
    """One order's executed part: what charges are worked from."""

    order_id: str
    instrument: Instrument
    product: Product
    side: Side
    qty: int  # executed
    value_paise: Fraction  # qty × average price (options: premium)


@dataclass(frozen=True)
class ChargeLine:
    label: str
    paise: int


@dataclass(frozen=True)
class ChargeBill:
    lines: tuple[ChargeLine, ...]  # in the order screens show them; zero lines included
    by_position: dict[str, int]  # "instrument_id|product" -> its total, for net P&L per row
    turnover_paise: int

    @property
    def total_paise(self) -> int:
        return sum(line.paise for line in self.lines)


class ChargesModel(Protocol):
    """Replaceable: a broker with its own way of charging gets its own model."""

    schedule: ChargeSchedule

    def bill(self, orders: Sequence[ExecutedOrder]) -> ChargeBill: ...


LABELS = (
    ("brokerage", "Brokerage"),
    ("stt", "STT"),
    ("exchange", "Exchange charges"),
    ("sebi", "SEBI fee"),
    ("ipft", "IPFT"),
    ("stamp", "Stamp duty"),
    ("gst", "GST"),
    ("dp", "DP charges"),
)


class ScheduleCharges:
    """Charges worked from a published schedule of rates."""

    def __init__(self, schedule: ChargeSchedule) -> None:
        self.schedule = schedule

    def bill(self, orders: Sequence[ExecutedOrder]) -> ChargeBill:
        """Each line is rounded to the paisa per instrument, then added up: a position's
        charges and the day's total always agree to the paisa."""
        exact: dict[str, dict[str, Fraction]] = {}  # by position key
        turnover = Fraction(0)
        for o in orders:
            parts = self._parts(o)
            if parts is None:
                continue
            key = position_key(o.instrument.id, o.product)
            mine = exact.setdefault(key, {k: Fraction(0) for k, _ in LABELS})
            if parts.pop("_dp_sale", None) and not mine["dp"]:  # once a stock a day
                mine["dp"] = Fraction(self._rates(o).dp_paise)  # type: ignore[union-attr]
            for part, amount in parts.items():
                mine[part] += amount
            turnover += o.value_paise
        rounded = {i: {k: _round(v) for k, v in parts.items()} for i, parts in exact.items()}
        lines = tuple(
            ChargeLine(label, sum(r[key] for r in rounded.values())) for key, label in LABELS
        )
        return ChargeBill(
            lines=lines,
            by_position={k: sum(r.values()) for k, r in sorted(rounded.items())},
            turnover_paise=_round(turnover),
        )

    def _rates(self, o: ExecutedOrder) -> SegmentRates | None:
        segment = segment_of(o.instrument, o.product)
        return None if segment is None else self.schedule.segments.get(segment)

    def _parts(self, o: ExecutedOrder) -> dict[str, Fraction] | None:
        rates = self._rates(o)
        if rates is None or o.qty <= 0:
            return None
        value = o.value_paise
        exchange = venue(o.instrument)
        pct = lambda p: value * p / 100  # noqa: E731
        if rates.brokerage_flat_paise is not None:
            brokerage = Fraction(rates.brokerage_flat_paise)
        else:
            brokerage = pct(rates.brokerage_pct)
            if rates.brokerage_cap_paise is not None:
                brokerage = min(brokerage, Fraction(rates.brokerage_cap_paise))
        buying = o.side is Side.BUY
        parts = {
            "brokerage": brokerage,
            "stt": pct(rates.stt_buy_pct if buying else rates.stt_sell_pct),
            "exchange": pct(rates.exchange_pct.get(exchange, Fraction(0))),
            "sebi": pct(self.schedule.sebi_pct),
            "ipft": pct(rates.ipft_pct.get(exchange, Fraction(0))),
            "stamp": pct(rates.stamp_buy_pct) if buying else Fraction(0),
        }
        taxable = parts["brokerage"] + parts["exchange"] + parts["sebi"] + parts["ipft"]
        parts["gst"] = taxable * self.schedule.gst_pct / 100
        if not buying and rates.dp_paise:
            parts["_dp_sale"] = Fraction(1)  # marker: once per stock per day (see bill)
        return parts


def bill_to_dict(bill: ChargeBill, schedule: ChargeSchedule) -> dict[str, Any]:
    return {
        "total_paise": bill.total_paise,
        "turnover_paise": bill.turnover_paise,
        "lines": [{"label": line.label, "paise": line.paise} for line in bill.lines],
        "by_position": bill.by_position,
        "schedule": {
            "name": schedule.name,
            "checked_on": schedule.checked_on.isoformat(),
            "source": schedule.source,
        },  # fmt: skip
    }


def _round(value: Fraction) -> int:
    """Nearest paisa, halves away from zero."""
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))


def executed(orders: Iterable[Any], instrument_of) -> list[ExecutedOrder]:
    """Executed orders from order views (orders/model.py OrderView) with fills."""
    result = []
    for view in orders:
        if view.filled_qty <= 0:
            continue
        instrument = instrument_of(view.request.instrument_id)
        if instrument is None:
            continue
        result.append(
            ExecutedOrder(
                order_id=view.client_order_id,
                instrument=instrument,
                product=view.request.product,
                side=view.request.side,
                qty=view.filled_qty,
                value_paise=Fraction(view.filled_qty * view.avg_price_paise),
            )
        )
    return result
