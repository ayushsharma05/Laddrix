"""Pre-trade risk checks: the first line of defence before an order can leave.

    order ─► kill switch ─► instrument allowed ─► product fits ─► size ─► value
          ─► price near the market ─► position (with working orders) ─► daily loss
          ─► open-order count ─► double click ─► the throttle, then the broker

Each check returns None (fine) or a reason a trader can read. The first problem wins.

Positions and P&L come from outside (positions service, K4) through small functions,
so these checks stay pure logic and easy to test. Worst-case positions count the
quantity still working in other orders: a resting buy of 75 counts as if it filled.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mocktrade.broker.rules import protected_limit
from mocktrade.domain.instruments import Instrument, InstrumentKind
from mocktrade.domain.money import format_rupees
from mocktrade.domain.orders import OrderRequest, OrderType, Side
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import OrderView
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import PRODUCTS_FOR_KIND, RiskLimits

OPTIONS = (InstrumentKind.CALL, InstrumentKind.PUT)


class RiskGate:
    """The pre-trade checks for one broker account, as an order gate (see orders.gate)."""

    def __init__(
        self,
        limits: RiskLimits,
        *,
        instruments: Callable[[str], Instrument | None],
        prices: Callable[[str], Tick | None],
        position: Callable[[str], int],  # net quantity now (+ long, - short)
        working: Callable[[], Sequence[OrderView]],  # this account's working orders
        day_pnl_paise: Callable[[], int],  # realised + unrealised today
        switches: Sequence[KillSwitch],  # e.g. the firm's and this account's
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),  # same clock as the orders
    ) -> None:
        self.limits = limits
        self._instrument = instruments
        self._price = prices
        self._position = position
        self._working = working
        self._pnl = day_pnl_paise
        self._switches = tuple(switches)
        self._clock = clock

    # --- the gate -------------------------------------------------------------------------

    async def admit_new(self, request: OrderRequest) -> str | None:
        instrument = self._instrument(request.instrument_id)
        if instrument is None:
            return f"unknown instrument {request.instrument_id}"
        tick = self._price(request.instrument_id)
        others = [o for o in self._working() if o.client_order_id != request.client_order_id]
        reason = (
            self._kill_problem()
            or self._instrument_problem(instrument, request, others)
            or self._product_problem(instrument, request)
            or self._size_problem(instrument, request.qty)
            or self._price_problem(instrument, request, tick)
            or self._value_problem(instrument, request, request.qty, tick)
            or self._position_problem(instrument, request, request.qty, others)
            or self._loss_problem(request, request.qty, others)
            or self._open_orders_problem(others)
            or self._duplicate_problem(request, others)
        )
        return reason

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        instrument = self._instrument(new.instrument_id)
        if instrument is None:
            return f"unknown instrument {new.instrument_id}"
        tick = self._price(new.instrument_id)
        others = [o for o in self._working() if o.client_order_id != order.client_order_id]
        leaves = new.qty - order.filled_qty
        grows = new.qty > order.request.qty
        return (
            self._kill_problem()
            or self._size_problem(instrument, new.qty)
            or self._price_problem(instrument, new, tick)
            or self._value_problem(instrument, new, new.qty, tick)
            or (self._position_problem(instrument, new, leaves, others) if grows else None)
            or (self._loss_problem(new, leaves, others) if grows else None)
        )

    async def admit_cancel(self, order: OrderView) -> str | None:
        return None  # cancelling only ever reduces risk

    # --- the checks -------------------------------------------------------------------------

    def _kill_problem(self) -> str | None:
        for switch in self._switches:
            if switch.engaged:
                state = switch.state
                return f"trading is stopped ({switch.name} kill switch: {state.reason})"
        return None

    def _instrument_problem(
        self, instrument: Instrument, request: OrderRequest, others
    ) -> str | None:
        limits = self.limits
        if instrument.exchange not in limits.allowed_exchanges:
            return f"{instrument.exchange.value} is not enabled for this account"
        if instrument.kind not in limits.allowed_kinds:
            return f"{instrument.kind.name.lower()} trading is not enabled for this account"
        if instrument.id in limits.blocked_instruments:
            return f"{instrument.symbol} is blocked for this account"
        if (
            instrument.kind in OPTIONS
            and request.side is Side.SELL
            and not limits.allow_option_selling
        ):
            short_after = self._position(instrument.id) - self._working_qty(
                instrument.id, Side.SELL, others
            )
            if short_after - request.qty < 0:
                return "selling options to open a short position is not enabled for this account"
        return None

    def _product_problem(self, instrument: Instrument, request: OrderRequest) -> str | None:
        allowed = PRODUCTS_FOR_KIND.get(instrument.kind, frozenset())
        if request.product not in allowed:
            names = ", ".join(sorted(p.value for p in allowed))
            return f"{request.product.value} can't be used for {instrument.symbol} (use {names})"
        return None

    def _size_problem(self, instrument: Instrument, qty: int) -> str | None:
        lots = qty // instrument.lot_size
        if lots > self.limits.max_order_lots:
            return (
                f"{_lots(qty, instrument)} is more than the {self.limits.max_order_lots}-lot "
                "limit per order"
            )
        return None

    def _price_problem(
        self, instrument: Instrument, request: OrderRequest, tick: Tick | None
    ) -> str | None:
        if tick is None or tick.ltp_paise <= 0:
            if request.order_type in (OrderType.MARKET, OrderType.SL_M):
                return f"no live price for {instrument.symbol}: market orders need one"
            if self.limits.require_market_price:
                return f"no live price for {instrument.symbol} to check this price against"
            return None
        band = self.limits.price_band_pct.get(instrument.kind)
        if band is None:
            return None
        for label, price in (("price", request.price_paise), ("trigger", request.trigger_paise)):
            if price is None:
                continue
            away = Decimal(abs(price - tick.ltp_paise)) * 100 / tick.ltp_paise
            if away > band:
                return (
                    f"{label} {format_rupees(price)} is {away:.1f}% from the last price "
                    f"{format_rupees(tick.ltp_paise)} (limit {band}%): check for a typo"
                )
        return None

    def _value_problem(
        self, instrument: Instrument, request: OrderRequest, qty: int, tick: Tick | None
    ) -> str | None:
        price = _worst_price(instrument, request, tick)
        if price is None:
            return None  # no price to value it with: _price_problem has already decided
        value = price * qty
        if value > self.limits.max_order_value_paise:
            return (
                f"order value {format_rupees(value)} is above the "
                f"{format_rupees(self.limits.max_order_value_paise)} limit per order"
            )
        return None

    def _position_problem(
        self, instrument: Instrument, request: OrderRequest, leaves: int, others
    ) -> str | None:
        """Would the worst case on this side go beyond the limit if everything filled?"""
        limit = self.limits.max_position_lots * instrument.lot_size
        now = self._position(instrument.id)
        working = self._working_qty(instrument.id, request.side, others)
        worst = now + request.side.sign * (working + leaves)
        if abs(worst) > limit and request.side.sign * worst > 0:
            verb = "buying" if request.side is Side.BUY else "selling"
            return (
                f"{instrument.symbol}: {verb} {_lots(leaves, instrument)} would make your "
                f"position up to {worst:+d} (limit ±{limit})"
            )
        return None

    def _loss_problem(self, request: OrderRequest, leaves: int, others) -> str | None:
        """Past the daily loss limit, only trades that close positions are allowed."""
        pnl = self._pnl()
        if pnl > -self.limits.max_daily_loss_paise:
            return None
        now = self._position(request.instrument_id)
        working = self._working_qty(request.instrument_id, request.side, others)
        after = now + request.side.sign * (working + leaves)
        closes = now != 0 and request.side.sign == -_sign(now) and _sign(after) != -_sign(now)
        if closes:
            return None
        return (
            f"daily loss limit reached ({format_rupees(-pnl)} lost, limit "
            f"{format_rupees(self.limits.max_daily_loss_paise)}): only closing trades are allowed"
        )

    def _open_orders_problem(self, others) -> str | None:
        if len(others) >= self.limits.max_open_orders:
            return f"{len(others)} orders are already working (limit {self.limits.max_open_orders})"
        return None

    def _duplicate_problem(self, request: OrderRequest, others) -> str | None:
        """An identical manual order still working and placed moments ago is a double click.
        Refused orders don't count, so retrying after a refusal always works."""
        if request.from_algo:
            return None  # algos send identical orders on purpose
        key = _duplicate_key(request)
        now = self._clock()
        for order in others:
            if not order.request.from_algo and _duplicate_key(order.request) == key:
                ago = (now - order.created_at).total_seconds()
                if 0 <= ago < self.limits.duplicate_window_s:
                    return (
                        f"an identical order was placed {ago:.1f} s ago: looks like a double "
                        f"click (wait {self.limits.duplicate_window_s:g} s to send it on purpose)"
                    )
        return None

    # --- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _working_qty(instrument_id: str, side: Side, others) -> int:
        return sum(
            o.leaves_qty
            for o in others
            if o.request.instrument_id == instrument_id and o.request.side is side
        )


def _worst_price(instrument: Instrument, request: OrderRequest, tick: Tick | None) -> int | None:
    """The price an order could trade at, at worst, for valuing it."""
    if request.order_type in (OrderType.LIMIT, OrderType.SL):
        return request.price_paise
    base = request.trigger_paise if request.order_type is OrderType.SL_M else None
    if base is None:
        base = tick.ltp_paise if tick is not None else None
    if base is None:
        return None
    return protected_limit(instrument, request.side, base, request.market_protection)


def _duplicate_key(request: OrderRequest) -> tuple:
    return (
        request.instrument_id,
        request.side,
        request.qty,
        request.order_type,
        request.price_paise,
        request.trigger_paise,
        request.product,
    )


def _lots(qty: int, instrument: Instrument) -> str:
    if instrument.lot_size == 1:
        return f"{qty} shares" if qty != 1 else "1 share"
    lots = qty // instrument.lot_size
    return f"{lots} lot{'s' if lots != 1 else ''} ({qty})"


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)
