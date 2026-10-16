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
(what the daily loss limit uses) starts at zero, like a broker's mark-to-market.
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

    @property
    def avg(self) -> Fraction:
        return self.cost / self.qty if self.qty else Fraction(0)

    def apply(self, side: Side, qty: int, price: int) -> None:
        signed = side.sign * qty
        if side is Side.BUY:
            self.buy_qty += qty
            self.buy_value += qty * price
        else:
            self.sell_qty += qty
            self.sell_value += qty * price
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
        self._listeners: list[Callable[[PositionView], None]] = []

    # --- feeding it --------------------------------------------------------------------------

    def on_order_event(self, event: OrderEvent) -> None:
        """Order manager listener: applies FILL events, ignores the rest."""
        if event.kind is not EventKind.FILL or event.fill is None or event.order is None:
            return
        if event.account_id != self.account_id:
            return
        self.replay(event.client_order_id, event.order.request, event.order.filled_qty,
                    event.fill.qty, event.fill.price_paise)  # fmt: skip

    def replay(
        self, client_order_id: str, request, cumulative: int, fill_qty: int, price_paise: int
    ) -> None:
        """Apply one fill of an order whose filled quantity is now `cumulative`. Live fills
        and fills read back from the audit trail after a restart both come here, so a fill
        is never counted twice: only the part beyond what this order already applied."""
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
        self.apply_fill(request.instrument_id, request.product, request.side, qty, price_paise)

    def apply_fill(
        self, instrument_id: InstrumentId, product: Product, side: Side, qty: int, price: int
    ) -> None:
        if qty <= 0 or price <= 0:
            raise ValueError(f"a fill needs a positive qty and price, got {qty} @ {price}")
        position = self._positions.setdefault((instrument_id, product), _Position())
        position.apply(side, qty, price)
        position.updated_at = self._clock()
        self._notify(instrument_id, product)

    def seed(
        self, instrument_id: InstrumentId, product: Product, qty: int, reference_paise: int
    ) -> None:
        """Start the day with a position carried from yesterday, at the previous close.

        It isn't counted as today's buy or sell; today's P&L starts at zero."""
        if (instrument_id, product) in self._positions:
            raise ValueError(f"{instrument_id} {product.value} already has a position today")
        self._positions[(instrument_id, product)] = _Position(
            qty=qty, cost=Fraction(qty * reference_paise), updated_at=self._clock()
        )
        self._ltp.setdefault(instrument_id, reference_paise)

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

    def subscribe(self, listener: Callable[[PositionView], None]) -> Callable[[], None]:
        """Call `listener` with the position each time it changes (fills and prices)."""
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
        )

    def _notify(self, instrument_id: InstrumentId, product: Product) -> None:
        if not self._listeners:
            return
        view = self.get(instrument_id, product)
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
