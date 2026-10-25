"""The position book: net positions and P&L for one broker account, from fills.

    FILL events (order manager) ─► apply_fill ─► position per (instrument, product)
    ticks (market data)         ─► mark       ─► unrealised P&L

How the numbers work (weighted average cost, as Indian brokers show it):
- Adding to a position: cost grows by qty × price; average = cost / qty.
- Reducing it: realised P&L = (exit price − average) × qty for longs (the reverse
  for shorts); the average of what's left doesn't change.
- Crossing zero (long 75, sell 150): close the 75, then open a short 75 at the fill.
- Unrealised = (last price − average) × qty.

Costs are kept as exact fractions, because an average like ₹100.075 isn't a whole
paisa. Rounding happens only when numbers are shown. That keeps the check
realised + unrealised == sells − buys + qty × last price exact to the paisa.

Fills are applied once: each order's cumulative filled quantity is remembered, so
replays (after a restart or a reconcile) never count twice.

Positions carried from yesterday are seeded at the previous close, so today's P&L
(what the daily loss limit uses) starts at zero, like a broker's mark-to-market. The
original buy price is kept too (`carried_avg_paise`), so the screens can show both.

A new trading day without a restart (#266): `roll()` starts it. Overnight positions are
re-seeded at the last price of the session that ended, with today's totals at zero; flat
positions are dropped; a delivery buy (CNC, long) leaves positions, as it is now a holding.
P&L here is gross: brokerage, STT, exchange charges, GST and stamp duty are not
included yet.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction

from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.orders import Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import EventKind, OrderEvent

log = logging.getLogger(__name__)

Key = tuple[InstrumentId, Product]


@dataclass(frozen=True)
class PositionView:
    """One position as screens show it. Money in paise (rounded to the nearest)."""

    instrument_id: InstrumentId
    product: Product
    qty: int  # + long, - short, 0 flat (kept so the day's realised P&L stays visible)
    avg_price_paise: Fraction  # exact; 0 when flat
    ltp_paise: int | None  # None until a price has been seen
    realised_paise: int
    unrealised_paise: int
    buy_qty: int  # today's totals, as on a broker's positions screen
    sell_qty: int
    buy_value_paise: int
    sell_value_paise: int
    updated_at: datetime | None
    carried_avg_paise: Fraction | None = None  # carried overnight: what it was bought at

    @property
    def pnl_paise(self) -> int:
        return self.realised_paise + self.unrealised_paise


@dataclass
class _Position:
    qty: int = 0
    cost: Fraction = Fraction(0)  # signed: qty × average (negative for shorts)
    realised: Fraction = Fraction(0)
    buy_qty: int = 0
    sell_qty: int = 0
    buy_value: int = 0
    sell_value: int = 0
    updated_at: datetime | None = None
    carried_avg: Fraction | None = None  # carried overnight: its average before the roll

    @property
    def avg(self) -> Fraction:
        return self.cost / self.qty if self.qty else Fraction(0)

    def apply(self, side: Side, qty: int, paise: int, value: int | None = None) -> None:
        """`value`: the fill's exact value when qty x price isn't it (#275); the price used
        is then value / qty, exactly."""
        price: int | Fraction = paise
        if value is None:
            value = qty * paise
        else:
            price = Fraction(value, qty)
        signed = side.sign * qty
        if side is Side.BUY:
            self.buy_qty += qty
            self.buy_value += value
        else:
            self.sell_qty += qty
            self.sell_value += value
        if self.qty == 0 or (self.qty > 0) == (signed > 0):  # opening or adding
            self.qty += signed
            self.cost += signed * price
            return
        closing = min(qty, abs(self.qty))  # reducing, maybe crossing zero
        avg = self.avg
        direction = 1 if self.qty > 0 else -1
        self.realised += (price - avg) * closing * direction
        self.qty -= direction * closing
        self.cost = avg * self.qty
        rest = qty - closing
        if rest:  # crossed zero: the rest opens the other way at the fill price
            self.qty = side.sign * rest
            self.cost = Fraction(self.qty * price)


class PositionBook:
    def __init__(
        self,
        account_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self._clock = clock
        self._positions: dict[Key, _Position] = {}
        self._ltp: dict[InstrumentId, int] = {}
        self._filled: dict[str, int] = {}  # client order id -> cumulative qty applied
        self._listeners: list[Callable[[PositionView], object]] = []

    # --- feeding it --------------------------------------------------------------------------

    def on_order_event(self, event: OrderEvent) -> None:
        """Order manager listener: applies FILL and CORRECTED events, ignores the rest."""
        if event.kind not in (EventKind.FILL, EventKind.CORRECTED):
            return
        if event.fill is None or event.order is None or event.account_id != self.account_id:
            return
        self.replay(event.client_order_id, event.order.request, event.order.filled_qty,
                    event.fill.qty, event.fill.price_paise, event.fill.value_paise)  # fmt: skip

    def replay(
        self,
        client_order_id: str,
        request,
        cumulative: int,
        fill_qty: int,
        price_paise: int,
        value_paise: int | None = None,
    ) -> None:
        """Apply one fill of an order whose filled quantity is now `cumulative`. Live fills
        and fills read back from the audit trail after a restart both come here, so a fill
        is never counted twice: only the part beyond what this order already applied.
        `value_paise`: the fill's exact value (#275); with `fill_qty` 0 it is a correction
        of what the order's earlier fills were worth."""
        if fill_qty == 0:
            if value_paise:
                self.correct(request.instrument_id, request.product, request.side, value_paise)
            return
        before = self._filled.get(client_order_id, 0)
        if cumulative <= before:
            log.debug("fill for %s already applied", client_order_id)
            return
        qty = cumulative - before
        if qty != fill_qty:
            log.warning(
                "fill qty %d for %s doesn't match filled %d -> %d; using %d",
                fill_qty,
                client_order_id,
                before,
                cumulative,
                qty,
            )
        self._filled[client_order_id] = cumulative
        value = value_paise if qty == fill_qty else None  # a part: its own price is all we have
        self.apply_fill(request.instrument_id, request.product, request.side, qty, price_paise,
                        value)  # fmt: skip

    def apply_fill(
        self,
        instrument_id: InstrumentId,
        product: Product,
        side: Side,
        qty: int,
        price: int,
        value: int | None = None,
    ) -> None:
        if qty <= 0 or price <= 0 or (value is not None and value <= 0):
            raise ValueError(f"a fill needs a positive qty and price, got {qty} @ {price}")
        position = self._positions.setdefault((instrument_id, product), _Position())
        position.apply(side, qty, price, value)
        position.updated_at = self._clock()
        self._notify(instrument_id, product)

    def seed(
        self,
        instrument_id: InstrumentId,
        product: Product,
        qty: int,
        reference_paise: int,
        carried_avg: Fraction | float | None = None,
    ) -> None:
        """Start the day with a position carried from yesterday, at the previous close.

        It isn't counted as today's buy or sell; today's P&L starts at zero."""
        if (instrument_id, product) in self._positions:
            raise ValueError(f"{instrument_id} {product.value} already has a position today")
        self._positions[(instrument_id, product)] = _Position(
            qty=qty,
            cost=Fraction(qty * reference_paise),
            updated_at=self._clock(),
            carried_avg=Fraction(carried_avg) if carried_avg is not None else None,
        )
        self._ltp.setdefault(instrument_id, reference_paise)

    def correct(self, instrument_id: InstrumentId, product: Product, side: Side,
                delta_paise: int) -> None:  # fmt: skip
        """The broker corrected what earlier fills on one side were worth, by `delta_paise`
        (+: buys cost more, or sells brought more), with nothing new traded (#275). While
        the position still holds that side, its average moves; once closed, the realised
        P&L does: a buy that cost ₹1 more is ₹1 less profit."""
        position = self._positions.get((instrument_id, product))
        if position is None or not delta_paise:
            return
        if side is Side.BUY:
            position.buy_value += delta_paise
        else:
            position.sell_value += delta_paise
        if position.qty and (position.qty > 0) == (side is Side.BUY):
            position.cost += side.sign * delta_paise  # still held: its cost moves
        else:
            position.realised -= side.sign * delta_paise  # closed: the profit moves
        position.updated_at = self._clock()
        self._notify(instrument_id, product)

    def settle(self, instrument_id: InstrumentId, product: Product, price: int) -> int:
        """Close a position at its final settlement price (a contract's expiry, #290). The
        price may be zero (an option that ends worthless), which no fill can be. The
        difference is realised as the trade that closes it would be. Returns the quantity
        settled (+ long, - short; 0: nothing open)."""
        position = self._positions.get((instrument_id, product))
        if position is None or not position.qty:
            return 0
        if price < 0:
            raise ValueError(f"a settlement price can't be negative, got {price}")
        qty = position.qty
        position.apply(Side.SELL if qty > 0 else Side.BUY, abs(qty), price)
        position.updated_at = self._clock()
        self._ltp[instrument_id] = price
        self._notify(instrument_id, product)
        return qty

    def roll(self) -> list[Key]:
        """Start a new trading day without a restart (#266): each open position carries over
        at the last price seen, with today's realised P&L and buy/sell totals at zero.
        Flat positions go; a delivery buy (CNC long) goes too (it is a holding now).
        Returns the positions carried."""
        now = self._clock()
        carried: dict[Key, _Position] = {}
        for (instrument_id, product), p in self._positions.items():
            if not p.qty or (product is Product.CNC and p.qty > 0):
                continue
            reference = self._ltp.get(instrument_id) or round(p.avg)
            carried[(instrument_id, product)] = _Position(
                qty=p.qty, cost=Fraction(p.qty * reference), updated_at=now,
                carried_avg=p.carried_avg if p.carried_avg is not None else p.avg,
            )  # fmt: skip
        gone = [key for key in self._positions if key not in carried]
        self._positions = carried
        for instrument_id, product in carried:
            self._notify(instrument_id, product)
        for key in gone:  # listeners hear it is flat with nothing today (never a None)
            self._tell(self._view(key, _Position(updated_at=now)))
        return list(carried)

    def mark(self, tick: Tick) -> None:
        """A new last price: unrealised P&L moves. Market data listener."""
        if tick.ltp_paise <= 0:
            return
        self._ltp[tick.instrument_id] = tick.ltp_paise
        for instrument_id, product in self._positions:
            if instrument_id == tick.instrument_id:
                self._notify(instrument_id, product)

    # --- reading -----------------------------------------------------------------------------

    def get(self, instrument_id: InstrumentId, product: Product) -> PositionView | None:
        position = self._positions.get((instrument_id, product))
        return self._view((instrument_id, product), position) if position else None

    def positions(self) -> list[PositionView]:
        return [self._view(key, p) for key, p in sorted(self._positions.items(), key=_order)]

    def net_qty(self, instrument_id: InstrumentId) -> int:
        """Net quantity across products: the real exposure (used by risk checks)."""
        return sum(p.qty for (i, _), p in self._positions.items() if i == instrument_id)

    def net_by_instrument(self) -> dict[InstrumentId, int]:
        """Net quantity per instrument across products (what brokers report as net)."""
        net: dict[InstrumentId, int] = {}
        for (instrument_id, _), position in self._positions.items():
            net[instrument_id] = net.get(instrument_id, 0) + position.qty
        return net

    def qty_by_product(self) -> dict[tuple[InstrumentId, Product], int]:
        """Quantity per instrument and product, as brokers list positions (reconciling)."""
        return {key: p.qty for key, p in self._positions.items()}

    def qty(self, instrument_id: InstrumentId, product: Product) -> int:
        """One product's quantity: what an order in that product can close."""
        position = self._positions.get((instrument_id, product))
        return position.qty if position else 0

    def realised_paise(self) -> int:
        return _round(sum((p.realised for p in self._positions.values()), Fraction(0)))

    def unrealised_paise(self) -> int:
        return _round(
            sum(
                (self._unrealised(i, p) for (i, _), p in self._positions.items()),
                Fraction(0),
            )
        )

    def day_pnl_paise(self) -> int:
        """Today's realised + unrealised, for the daily loss limit."""
        total = sum(
            (p.realised + self._unrealised(i, p) for (i, _), p in self._positions.items()),
            Fraction(0),
        )
        return _round(total)

    def subscribe(self, listener: Callable[[PositionView], object]) -> Callable[[], None]:
        """Call `listener` with the position each time it changes (fills and prices). What
        it returns is ignored."""
        self._listeners.append(listener)

        def stop() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return stop

    # --- inside ------------------------------------------------------------------------------

    def _unrealised(self, instrument_id: InstrumentId, position: _Position) -> Fraction:
        ltp = self._ltp.get(instrument_id)
        if ltp is None or position.qty == 0:
            return Fraction(0)
        return ltp * position.qty - position.cost

    def _view(self, key: Key, position: _Position) -> PositionView:
        instrument_id, product = key
        return PositionView(
            instrument_id=instrument_id,
            product=product,
            qty=position.qty,
            avg_price_paise=position.avg,
            ltp_paise=self._ltp.get(instrument_id),
            realised_paise=_round(position.realised),
            unrealised_paise=_round(self._unrealised(instrument_id, position)),
            buy_qty=position.buy_qty,
            sell_qty=position.sell_qty,
            buy_value_paise=position.buy_value,
            sell_value_paise=position.sell_value,
            updated_at=position.updated_at,
            carried_avg_paise=position.carried_avg,
        )

    def _notify(self, instrument_id: InstrumentId, product: Product) -> None:
        if not self._listeners:
            return
        view = self.get(instrument_id, product)
        if view is not None:
            self._tell(view)

    def _tell(self, view: PositionView) -> None:
        for listener in list(self._listeners):
            try:
                listener(view)
            except Exception:  # a broken screen must never stop position keeping
                log.exception("position listener failed")


def _round(value: Fraction) -> int:
    """Nearest paisa, halves away from zero (so +0.5 and -0.5 round symmetrically)."""
    if value >= 0:
        return int(value + Fraction(1, 2))
    return -int(-value + Fraction(1, 2))


def _order(item: tuple[Key, _Position]) -> tuple[str, str]:
    (instrument_id, product), _ = item
    return instrument_id, product.value
