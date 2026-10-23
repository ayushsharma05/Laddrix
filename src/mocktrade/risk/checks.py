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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from mocktrade.broker.rules import protected_limit
from mocktrade.domain.instruments import Instrument, InstrumentKind
from mocktrade.domain.money import format_rupees
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import OrderView
from mocktrade.refdata.expiry import ExpiryInfo
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import PRODUCTS_FOR_KIND, RiskLimits

OPTIONS = (InstrumentKind.CALL, InstrumentKind.PUT)


MAX_PRICE_AGE_S = 60.0  # an order that adds risk needs a price at most this old (#267)


class RiskGate:
    """The pre-trade checks for one broker account, as an order gate (see orders.gate)."""

    recheck_after_wait = True  # run again if a later gate (the throttle) made the order wait

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
        positions: Callable[[], dict[str, int]] = dict,  # every net position (covered shorts)
        product_position: Callable[[str, Product], int] | None = None,  # one product's qty
        price_age: Callable[[str], float | None] = lambda _i: None,  # s since its last tick
        max_price_age_s: float = MAX_PRICE_AGE_S,
        expiry: Callable[[Instrument, datetime], ExpiryInfo | None] = lambda _i, _n: None,
    ) -> None:
        self._expiry = expiry  # a contract's expiry facts at a moment (#290)
        self._price_age = price_age
        self.max_price_age_s = max_price_age_s
        self.limits = limits  # replaced whole by an admin's edit; read fresh on every order
        self.off_reason: str | None = None  # set: the account takes only closing trades
        self._instrument = instruments
        self._price = prices
        self._position = position
        self._working = working
        self._pnl = day_pnl_paise
        self._switches = tuple(switches)
        self._clock = clock
        self._positions = positions
        # what an order can close is its own product's position (#258); net when not given
        self._product_position = product_position or (lambda i, _p: position(i))

    # --- the gate -------------------------------------------------------------------------

    async def admit_new(self, request: OrderRequest) -> str | None:
        instrument = self._instrument(request.instrument_id)
        if instrument is None:
            return f"unknown instrument {request.instrument_id}"
        tick = self._price(request.instrument_id)
        others = [o for o in self._working() if o.client_order_id != request.client_order_id]
        reason = (
            self._kill_problem(request)
            or self._off_problem(request, request.qty, others)
            or self._expiry_problem(instrument, request, request.qty, others)
            or self._instrument_problem(instrument, request, others)
            or self._product_problem(instrument, request)
            or self._size_problem(instrument, request.qty)
            or self._price_problem(instrument, request, tick)
            or self._stale_problem(instrument, request, request.qty, others)
            or self._value_problem(instrument, request, request.qty, tick)
            or self._position_problem(instrument, request, request.qty, others)
            or self._loss_problem(request, request.qty, others)
            or self._open_orders_problem(others)
            or self._duplicate_problem(request, others)
            or self._reduce_only_problem(request, request.qty, others)
        )
        return reason

    async def check_basket(self, requests: Sequence[OrderRequest]) -> list[str | None]:
        """Every leg of a basket through the same checks, each leg seeing the legs before it
        as already working (open-order count, position worst case). Sends nothing."""
        real = self._working
        pending: list[_Pending] = []
        results: list[str | None] = []
        try:
            for n, request in enumerate(requests, 1):
                # legs have no order id yet: a stand-in keeps them apart from each other
                request = replace(request, client_order_id=f"(basket leg {n})")
                self._working = lambda p=tuple(pending): [*real(), *p]
                results.append(await self.admit_new(request))
                pending.append(_Pending(request, self._clock()))
        finally:
            self._working = real
        return results

    async def admit_at(self, request: OrderRequest, ltp_paise: int) -> str | None:
        """The checks for an order that will only be placed when the price reaches
        `ltp_paise` (a GTT leg at its trigger): price bands and values are judged there."""
        real = self._price
        try:
            self._price = lambda i: Tick(i, ltp_paise) if i == request.instrument_id else real(i)
            return await self.admit_new(request)
        finally:
            self._price = real

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
            or self._expiry_problem(instrument, new, leaves, others, grows=grows)
            or self._size_problem(instrument, new.qty)
            or self._price_problem(instrument, new, tick)
            or self._value_problem(instrument, new, new.qty, tick)
            or (
                self._instrument_problem(instrument, replace(new, qty=leaves), others)
                if grows
                else None
            )
            or (self._stale_problem(instrument, new, leaves, others) if grows else None)
            or (self._position_problem(instrument, new, leaves, others) if grows else None)
            or (self._loss_problem(new, leaves, others) if grows else None)
            or (self._off_problem(new, leaves, others) if grows else None)
            or (self._reduce_only_problem(new, leaves, others) if grows else None)
        )

    async def admit_cancel(self, order: OrderView) -> str | None:
        return None  # cancelling only ever reduces risk

    # --- the checks -------------------------------------------------------------------------

    def _reduce_only_problem(
        self, request: OrderRequest, qty: int, others: Sequence[OrderView]
    ) -> str | None:
        """A close (reduce-only) may close what is still open in its product, no more: other
        closes on their way (any outcome, known or not) have taken their part (#261)."""
        if not request.reduce_only:
            return None
        held = self._product_position(request.instrument_id, request.product)
        closable = held if request.side is Side.SELL else -held  # what this side reduces
        taken = sum(
            o.leaves_qty
            for o in others
            if o.request.reduce_only
            and o.request.instrument_id == request.instrument_id
            and o.request.product is request.product
            and o.request.side is request.side
        )
        if closable <= 0:
            return f"nothing to close: no {request.product.value} position this order reduces"
        if qty > closable - taken:
            return (
                f"would close more than is open: {closable} open in {request.product.value}, "
                f"{taken} already being closed"
            )
        return None

    def _kill_problem(self, request: OrderRequest | None = None) -> str | None:
        """A kill switch blocks every new order and change, except a forced close by the exit
        desk that only reduces (#262): it still passes every other check below, and the
        reduce-only check can't let it reverse anything. Changes are always blocked."""
        if request is not None and request.forced_close and request.reduce_only:
            return None
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
            and not self._covered(instrument, request, others)
        ):
            return (
                "selling options to open a short position is not enabled for this account "
                "(a short covered by a filled long option of the same type and expiry is)"
            )
        return None

    def _covered(self, instrument: Instrument, request: OrderRequest, others) -> bool:
        """After this sell, every short in the family (same underlying, type and expiry) is
        still covered by longs: a spread with a capped loss, never a naked short.

        Worst case: every working sell fills. Only FILLED longs cover: a working buy may never
        fill (or be cancelled), so it protects nothing yet. The one exception is a basket's
        preview, where its own earlier buy legs count: the basket sends no short leg until
        those buys have filled (orders/basket.py). Checking every option sell, not only one
        that opens a short, also stops selling the long that protects an open short."""

        def family(other_id: str) -> bool:
            other = self._instrument(other_id)
            return (
                other is not None
                and other.kind is instrument.kind
                and other.underlying == instrument.underlying
                and other.expiry == instrument.expiry
            )

        net = {i: q for i, q in self._positions().items() if family(i)}
        net[request.instrument_id] = self._position(request.instrument_id)  # always this one
        for o in others:
            i = o.request.instrument_id
            if not family(i):
                continue
            if o.request.side is Side.SELL:
                net[i] = net.get(i, 0) - o.leaves_qty
            elif getattr(o, "planned", False):  # an earlier buy leg of the basket being checked
                net[i] = net.get(i, 0) + o.leaves_qty
        net[request.instrument_id] = net.get(request.instrument_id, 0) - request.qty
        longs = sum(q for q in net.values() if q > 0)
        shorts = -sum(q for q in net.values() if q < 0)
        return longs >= shorts

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
        if self._closes(request, leaves, others):
            return None
        return (
            f"daily loss limit reached ({format_rupees(-pnl)} lost, limit "
            f"{format_rupees(self.limits.max_daily_loss_paise)}): only closing trades are allowed"
        )

    def _expiry_problem(self, instrument: Instrument, request: OrderRequest, leaves: int,
                        others, *, grows: bool = True) -> str | None:  # fmt: skip
        """A contract that has expired can't be traded at all, not even to close: the
        exchange has settled it (#290). After the broker's cut-off on its expiry day, only
        closing trades go."""
        info = self._expiry(instrument, self._clock())
        if info is None:
            return None
        if info.expired:
            return f"{info.note}; its position settles at the final settlement price"
        if not info.opening_closed or not grows:
            return None
        if request.reduce_only or self._closes(request, leaves, others):
            return None
        cutoff = info.opening_cutoff.strftime("%H:%M") if info.opening_cutoff else ""
        return (f"{instrument.symbol} expires today: from {cutoff} only closing trades are "
                f"allowed ({info.settlement} settlement)")  # fmt: skip

    def _stale_problem(self, instrument: Instrument, request: OrderRequest, leaves: int,
                       others) -> str | None:  # fmt: skip
        """An order that adds risk needs this instrument's own price to be recent (#267): a
        busy other instrument can't hide that this one has gone quiet. Closing trades go."""
        age = self._price_age(request.instrument_id)
        if age is None or age <= self.max_price_age_s:
            return None  # no price at all is _price_problem's to judge
        if request.reduce_only or self._closes(request, leaves, others):
            return None
        return (f"the last price for {instrument.symbol} is {age:.0f} s old (limit "
                f"{self.max_price_age_s:.0f} s): open its ladder or wait for a new price; "
                "closing trades still go")  # fmt: skip

    def _off_problem(self, request: OrderRequest, leaves: int, others) -> str | None:
        """A turned-off account: only trades that close positions (an admin's switch)."""
        if self.off_reason is None or self._closes(request, leaves, others):
            return None
        return f"{self.off_reason}: only closing trades are allowed"

    def _closes(self, request: OrderRequest, leaves: int, others) -> bool:
        """Does this order only reduce its product's position (with what already works on that
        side in that product)? A MIS sell doesn't close an NRML long: at the broker it opens a
        MIS short (#258)."""
        now = self._product_position(request.instrument_id, request.product)
        working = sum(
            o.leaves_qty for o in others
            if o.request.instrument_id == request.instrument_id and o.request.side is request.side
            and o.request.product is request.product
        )  # fmt: skip
        after = now + request.side.sign * (working + leaves)
        return now != 0 and request.side.sign == -_sign(now) and _sign(after) != -_sign(now)

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
            if request.basket_id and order.request.basket_id == request.basket_id:
                continue  # one basket's legs (e.g. a close split in pieces) are meant
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


@dataclass(frozen=True)
class _Pending:
    """An earlier basket leg, counted as if it were already working."""

    request: OrderRequest
    created_at: datetime
    planned: bool = True  # not working yet: the basket sends it before anything it covers

    @property
    def client_order_id(self) -> str:
        return self.request.client_order_id

    @property
    def leaves_qty(self) -> int:
        return self.request.qty


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
