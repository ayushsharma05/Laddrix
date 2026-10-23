"""The paper broker: fake money, real prices.

It follows the same Broker interface as Kite, so nothing above it can tell the
difference. Fills are deliberately conservative, so paper results don't flatter you:

1. Marketable orders (a buy at or above the best ask) fill at once against the
   visible 5-level depth, level by level at each level's price, never more than the
   quantity shown. Depth used by one order isn't available to the next in the same tick.
2. Resting orders remember the quantity ahead of them at their price. Volume traded
   at that price uses up the queue ahead first; only then do we fill. Our own orders
   share that volume in arrival order, never each counting it again. If the price
   trades through our level, or the other side moves onto our price, we fill at OUR
   price (a resting order never gets price improvement).
   Volume only counts from the tick after we joined: the tick we read our queue place
   from already happened before we arrived.
   Changing the price or raising the quantity sends an order to the back of the queue;
   lowering the quantity keeps its place, as on NSE/BSE.
3. Market and SL-M orders become limits at LTP ± market protection, like the real rule.
   Stop orders wait until the LTP reaches the trigger, then arrive like a new order.
4. Every acknowledgement waits a configurable latency, as a real broker would.
5. The same rules as the real broker apply: request validation, the modification
   cap, and refusing changes to finished orders.

Use `full` mode ticks: without depth and volume, resting orders only fill when the
price trades through them.
"""

import asyncio
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from mocktrade.broker.base import BasketMargin, BrokerError, BrokerPosition
from mocktrade.broker.paper_funds import Exposure, PaperMargins, paper_funds, used_margin
from mocktrade.broker.rules import (
    MAX_MODIFICATIONS_PER_ORDER,
    order_spec,
    protected_limit,
    stop_trigger_problem,
)
from mocktrade.domain.gtt import (
    GttKind,
    GttRequest,
    GttSpec,
    GttStatus,
    GttView,
    gtt_problems,
    standard_gtt_spec,
)
from mocktrade.domain.instruments import Instrument, InstrumentId
from mocktrade.domain.order_spec import OrderSpec, products_for
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
    Validity,
)
from mocktrade.domain.portfolio import Funds, Holding, Trade
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.positions.book import PositionBook
from mocktrade.refdata.store import InstrumentStore

UNKNOWN_QUEUE = 10**12  # our price is outside the visible depth: assume a long queue


@dataclass
class _PaperOrder:
    request: OrderRequest
    broker_id: str
    instrument: Instrument
    qty: int
    limit_paise: int | None  # None until a stop triggers or a market order is priced
    triggered: bool
    status: OrderStatus = OrderStatus.PENDING
    filled: int = 0
    notional: int = 0  # sum of fill qty × price, for the average
    queue_ahead: int = UNKNOWN_QUEUE
    modifications: int = 0
    accepted: bool = False
    seq: int = 0  # arrival order, for time priority among our own orders
    queued_on: Tick | None = None  # the tick our queue place was read from

    @property
    def leaves(self) -> int:
        return self.qty - self.filled

    @property
    def avg_price(self) -> int:
        return self.notional // self.filled if self.filled else 0


@dataclass
class _Book:
    """What we've already used of the latest tick, so two orders can't take the same depth."""

    tick: Tick | None = None
    used: dict[tuple[str, int], int] = field(default_factory=dict)  # (side, price) -> qty used
    traded_left: dict[int, int] = field(default_factory=dict)  # price -> volume not yet shared


@dataclass
class _PaperGtt:
    gtt_id: str
    request: GttRequest
    placed_at_paise: int  # the last price when placed: which way a SINGLE trigger is crossed
    status: GttStatus = GttStatus.ACTIVE
    order_id: str | None = None
    fired_leg: int | None = None
    reason: str = ""

    def touched(self, ltp: int) -> int | None:
        """The leg the last price has touched, if any."""
        legs = self.request.legs
        if self.request.kind is GttKind.OCO:
            if ltp <= legs[0].trigger_paise:
                return 0
            return 1 if ltp >= legs[1].trigger_paise else None
        trigger = legs[0].trigger_paise
        rising = trigger > self.placed_at_paise
        return 0 if (ltp >= trigger if rising else ltp <= trigger) else None

    def view(self) -> GttView:
        return GttView(self.gtt_id, self.request, self.status, order_id=self.order_id,
                       fired_leg=self.fired_leg, reason=self.reason)  # fmt: skip


class PaperBroker:
    def __init__(
        self,
        market_data: MarketData,
        instruments: InstrumentStore,
        *,
        latency_s: float = 0.08,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        name: str = "paper",
        starting_paise: int = 10_00_000_00,  # ₹10 lakh of paper money
        margins: PaperMargins | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        every_tick: bool = False,
    ) -> None:
        """`every_tick`: see every tick as it arrives (MarketData.tap) instead of the newest per
        instrument (a subscription, which skips ticks a busy loop couldn't take). A replay at
        full speed needs it: the same day must give the same fills at any speed."""
        self.name = name
        self.every_tick = every_tick
        self.starting_paise = starting_paise
        self.margins = margins or PaperMargins()
        self._clock = clock
        self._market_data = market_data
        self._instruments = instruments
        self._latency_s = latency_s
        self._sleep = sleep
        self._orders: dict[str, _PaperOrder] = {}
        self._by_instrument: dict[InstrumentId, set[str]] = {}
        self._books: dict[InstrumentId, _Book] = {}
        self._last_volume: dict[InstrumentId, int] = {}
        self._queue: asyncio.Queue[OrderUpdate] = asyncio.Queue()
        self._tasks: set[asyncio.Task] = set()
        self._ids = itertools.count(1)
        self._subscription = market_data.subscribe(every_tick=True)  # fills see every price
        if every_tick:
            market_data.tap(self.on_tick)
        self._runner: asyncio.Task | None = None
        self._gtts: dict[str, _PaperGtt] = {}
        self._gtt_ids = itertools.count(1)
        # the account as the paper broker keeps it (positions, trades, holdings, money)
        self._trades: list[Trade] = []  # today's fills here
        self._trade_ids = itertools.count(1)
        self._carried: dict[tuple[InstrumentId, Product], tuple[int, int]] = {}
        self._adopted_trades: list[Trade] = []  # today's fills before a restart
        self._holdings: list[Holding] = []
        self._earlier_pnl_paise = 0
        self._book = PositionBook(name)

    # --- Broker interface --------------------------------------------------------------

    def order_spec(self, instrument: Instrument) -> OrderSpec:
        """Paper offers exactly what Kite offers, so paper can't be more lenient."""
        return order_spec(instrument)

    async def place(self, request: OrderRequest) -> str:
        instrument = self._instruments.get(request.instrument_id)
        if instrument is None:
            raise BrokerError(f"unknown instrument {request.instrument_id}")
        try:
            request.validate(instrument)
        except ValueError as exc:
            raise BrokerError(str(exc)) from None
        self._check_trigger(request)
        seq = next(self._ids)
        broker_id = f"P{seq}"
        order = _PaperOrder(
            seq=seq,
            request=request,
            broker_id=broker_id,
            instrument=instrument,
            qty=request.qty,
            limit_paise=request.price_paise if request.order_type is OrderType.LIMIT else None,
            triggered=request.order_type not in (OrderType.SL, OrderType.SL_M),
        )
        self._orders[broker_id] = order
        self._by_instrument.setdefault(instrument.id, set()).add(broker_id)
        if not self.every_tick:
            self._subscription.add([instrument.id])
        self._spawn(self._accept(order))
        return broker_id

    async def modify(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        price_paise: int | None = None,
        trigger_paise: int | None = None,
    ) -> None:
        order = self._working(broker_order_id)
        if order.modifications >= MAX_MODIFICATIONS_PER_ORDER:
            raise BrokerError(
                f"order already modified {MAX_MODIFICATIONS_PER_ORDER} times: cancel and re-place"
            )
        new_request = order.request
        try:
            if qty is not None:
                order.instrument.check_quantity(qty)
                if qty <= order.filled:
                    raise ValueError(f"new quantity {qty} is not above the filled {order.filled}")
                new_request = replace(new_request, qty=qty)
            if price_paise is not None:
                new_request = replace(new_request, price_paise=price_paise)
            if trigger_paise is not None:
                new_request = replace(new_request, trigger_paise=trigger_paise)
            new_request.validate(order.instrument)
        except ValueError as exc:
            raise BrokerError(str(exc)) from None
        if trigger_paise is not None and not order.triggered:
            self._check_trigger(new_request)

        price_changed = price_paise is not None and price_paise != order.request.price_paise
        qty_raised = qty is not None and qty > order.qty
        order.modifications += 1
        order.request = new_request
        order.qty = new_request.qty
        if price_changed and order.request.order_type in (OrderType.LIMIT, OrderType.SL):
            if order.triggered:
                order.limit_paise = price_paise
        loses_place = price_changed or qty_raised
        if loses_place:
            order.queue_ahead = UNKNOWN_QUEUE  # back of the queue at the (new) price
        self._spawn(self._after_modify(order, loses_place))

    def _check_trigger(self, request: OrderRequest) -> None:
        """Refuse a stop the market has already passed, as Kite does (see rules.py)."""
        if request.order_type not in (OrderType.SL, OrderType.SL_M):
            return
        tick = self._market_data.latest(request.instrument_id)
        if tick is None or request.trigger_paise is None:
            return  # no price yet: nothing to compare with
        problem = stop_trigger_problem(request.side, request.trigger_paise, tick.ltp_paise)
        if problem:
            raise BrokerError(problem)

    async def cancel(self, broker_order_id: str) -> None:
        order = self._working(broker_order_id)
        self._spawn(self._cancel(order, "cancelled by user"))

    async def updates(self) -> AsyncIterator[OrderUpdate]:
        while True:
            yield await self._queue.get()

    async def orders(self) -> list[OrderUpdate]:
        return [self._snapshot(o) for o in self._orders.values()]

    durable = False  # its orders live in memory: a restart forgets them (recovery.py)

    def adopt(
        self,
        *,
        carried: dict[tuple[InstrumentId, Product], tuple[int, int]] | None = None,
        trades: Iterable[Trade] = (),
        holdings: Iterable[Holding] = (),
        earlier_pnl_paise: int = 0,
    ) -> None:
        """After a restart (recovery.py), what the paper exchange would still know: positions
        carried in from earlier days ((instrument, product) -> (qty, the previous close)),
        today's fills before the restart, delivery holdings, and earlier days' P&L."""
        self._carried = {k: v for k, v in (carried or {}).items() if v[0]}
        self._adopted_trades = list(trades)
        self._holdings = [h for h in holdings if h.total_qty]
        self._earlier_pnl_paise = earlier_pnl_paise
        book = PositionBook(self.name, clock=self._clock)
        for (instrument_id, product), (qty, close) in sorted(self._carried.items(), key=str):
            book.seed(instrument_id, product, qty, close)
        for t in [*self._adopted_trades, *self._trades]:
            book.apply_fill(t.instrument_id, t.product, t.side, t.qty, t.price_paise)
        self._book = book

    async def positions(self) -> list[BrokerPosition]:
        """One row per instrument and product, as Kite lists them (for reconciling)."""
        return [
            BrokerPosition(
                p.instrument_id, p.qty, round(p.avg_price_paise) if p.qty else 0, product=p.product
            )  # fmt: skip
            for p in self._book.positions()
        ]

    # --- holdings, funds, tradebook (what the account holds and owns) -------------------

    async def trades(self) -> list[Trade]:
        return [*self._adopted_trades, *self._trades]

    async def holdings(self) -> list[Holding]:
        """Delivery holdings from earlier days, marked at the latest price. Today's delivery
        buys are positions until tomorrow, as at Kite; today's sales show as `used_qty`."""
        sold: dict[InstrumentId, int] = {}
        for t in await self.trades():
            if t.product is Product.CNC:
                sold[t.instrument_id] = sold.get(t.instrument_id, 0) - t.side.sign * t.qty
        result = []
        for h in self._holdings:
            tick = self._market_data.latest(h.instrument_id)
            result.append(
                replace(
                    h,
                    used_qty=min(max(sold.get(h.instrument_id, 0), 0), h.total_qty),
                    ltp_paise=tick.ltp_paise if tick and tick.ltp_paise > 0 else h.ltp_paise,
                    close_paise=(tick.close_paise if tick and tick.close_paise else h.close_paise),
                )
            )
        return result

    async def funds(self) -> list[Funds]:
        """Paper money, worked out by paper_funds.py (an estimate: plain margin rates)."""
        exposures: dict[tuple[InstrumentId, Product], dict] = {}
        for p in self._book.positions():
            if (tick := self._market_data.latest(p.instrument_id)) is not None:
                self._book.mark(tick)
        for p in self._book.positions():
            exposures[(p.instrument_id, p.product)] = {"qty": p.qty}
        for o in self._orders.values():
            if o.status.is_final or not o.leaves:
                continue
            e = exposures.setdefault((o.instrument.id, o.request.product), {"qty": 0})
            side = "buy" if o.request.side is Side.BUY else "sell"
            e[f"{side}_leaves"] = e.get(f"{side}_leaves", 0) + o.leaves
            if o.limit_paise:  # the worst price any of them would pay
                worst = max if side == "buy" else min
                e[f"{side}_price"] = worst(e.get(f"{side}_price", o.limit_paise), o.limit_paise)
        built = []
        for (instrument_id, product), e in exposures.items():
            instrument = self._instruments.get(instrument_id)
            if instrument is None:
                continue
            tick = self._market_data.latest(instrument_id)
            view = self._book.get(instrument_id, product)
            price = tick.ltp_paise if tick and tick.ltp_paise > 0 else 0
            price = price or (round(view.avg_price_paise) if view else 0)
            price = price or e.get("buy_price") or e.get("sell_price") or 0
            built.append(Exposure(instrument, product, price=price, **e))
        cost = sum(h.invested_paise for h in self._holdings)
        return [
            paper_funds(
                starting_paise=self.starting_paise,
                earlier_pnl_paise=self._earlier_pnl_paise,
                holdings_cost_paise=cost,
                realised_paise=self._book.realised_paise(),
                unrealised_paise=self._book.unrealised_paise(),
                exposures=built,
                rates=self.margins,
            )
        ]

    async def basket_margin(self, requests: list[OrderRequest]) -> BasketMargin:
        """What these orders would block if they all filled, with today's positions (an
        estimate from PaperMargins, like the paper funds). `initial`: each order on its own
        from flat; `final`: the extra over what the positions use now (an exit adds 0)."""
        now: dict[tuple[InstrumentId, Product], list[int]] = {}  # -> [qty now, qty after, price]
        initial = 0
        for p in self._book.positions():
            now[(p.instrument_id, p.product)] = [p.qty, p.qty, self._price_of(p.instrument_id)]
        for r in requests:
            instrument = self._instruments.get(r.instrument_id)
            if instrument is None:
                raise BrokerError(f"unknown instrument {r.instrument_id}")
            price = r.price_paise or r.trigger_paise or self._price_of(r.instrument_id)
            if not price:
                raise BrokerError(f"no price yet for {r.instrument_id}")
            row = now.setdefault((r.instrument_id, r.product), [0, 0, price])
            row[1] += r.side.sign * r.qty
            row[2] = row[2] or price
            alone = Exposure(instrument, r.product, r.side.sign * r.qty, price)
            initial += round(sum(used_margin(self.margins, [alone])[0].values()))

        def used(after: bool) -> int:
            exposures = []
            for (instrument_id, product), (qty, later, price) in now.items():
                instrument = self._instruments.get(instrument_id)
                if instrument is not None:
                    exposures.append(Exposure(instrument, product, later if after else qty, price))
            return round(sum(used_margin(self.margins, exposures)[0].values()))

        return BasketMargin(initial_paise=initial, final_paise=max(0, used(True) - used(False)))

    def _price_of(self, instrument_id: InstrumentId) -> int:
        tick = self._market_data.latest(instrument_id)
        return tick.ltp_paise if tick is not None and tick.ltp_paise > 0 else 0

    # --- GTT, as Kite holds them -------------------------------------------------------
    # A trigger touched by the last price places a LIMIT order *from the broker* (a client id
    # "GTT<id>" we never issued), exactly as Kite does: MockTrade adopts it (orders/gtt.py).
    # Like paper orders, paper GTTs live in memory: a restart forgets them.

    def gtt_spec(self, instrument: Instrument) -> GttSpec:
        return standard_gtt_spec(instrument, products_for(instrument))

    async def place_gtt(self, request: GttRequest, ltp_paise: int) -> str:
        self._check_gtt(request)
        gtt_id = f"G{next(self._gtt_ids)}"
        self._gtts[gtt_id] = _PaperGtt(gtt_id, request, ltp_paise)
        self._subscription.add([request.instrument_id])
        await self._sleep(self._latency_s)
        return gtt_id

    async def modify_gtt(self, gtt_id: str, request: GttRequest, ltp_paise: int) -> None:
        gtt = self._active_gtt(gtt_id)
        self._check_gtt(request)
        gtt.request, gtt.placed_at_paise = request, ltp_paise
        await self._sleep(self._latency_s)

    async def delete_gtt(self, gtt_id: str) -> None:
        self._active_gtt(gtt_id).status = GttStatus.DELETED
        await self._sleep(self._latency_s)

    async def gtts(self) -> list[GttView]:
        return [g.view() for g in self._gtts.values()]

    def _check_gtt(self, request: GttRequest) -> None:
        instrument = self._instruments.get(request.instrument_id)
        if instrument is None:
            raise BrokerError(f"unknown instrument {request.instrument_id}")
        tick = self._market_data.latest(instrument.id)
        problems = gtt_problems(
            self.gtt_spec(instrument), request, tick.ltp_paise if tick else None
        )
        if problems:
            raise BrokerError("; ".join(problems))

    def _active_gtt(self, gtt_id: str) -> "_PaperGtt":
        gtt = self._gtts.get(gtt_id)
        if gtt is None:
            raise BrokerError(f"no GTT {gtt_id}")
        if gtt.status is not GttStatus.ACTIVE:
            raise BrokerError(f"GTT {gtt_id} is {gtt.status.value}")
        return gtt

    def _check_gtts(self, tick: Tick) -> None:
        for gtt in self._gtts.values():
            if (
                gtt.status is not GttStatus.ACTIVE
                or gtt.request.instrument_id != tick.instrument_id
            ):
                continue
            leg = gtt.touched(tick.ltp_paise)
            if leg is not None:
                gtt.status, gtt.fired_leg = GttStatus.TRIGGERED, leg
                self._spawn(self._fire_gtt(gtt, leg))

    async def _fire_gtt(self, gtt: "_PaperGtt", n: int) -> None:
        leg, r = gtt.request.legs[n], gtt.request
        order = OrderRequest(
            client_order_id=f"GTT{gtt.gtt_id}", account_id=r.account_id, user_id=r.user_id,
            instrument_id=r.instrument_id, side=leg.side, qty=leg.qty,
            order_type=OrderType.LIMIT, product=r.product, price_paise=leg.price_paise,
        )  # fmt: skip
        try:
            gtt.order_id = await self.place(order)
        except BrokerError as exc:
            gtt.reason = f"the order was refused: {exc.reason}"

    # --- running against live prices ---------------------------------------------------

    def start(self) -> None:
        """Begin reading live ticks from the market data cache (with `every_tick`, the tap
        already delivers them: nothing to start)."""
        if self._runner is None and not self.every_tick:
            self._runner = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._runner = None
        self._subscription.close()
        for task in list(self._tasks):
            task.cancel()

    @property
    def idle(self) -> bool:
        """Nothing under way: no acknowledgement pending, every update taken (backtests)."""
        return all(t.done() for t in self._tasks) and self._queue.empty()

    async def settle(self) -> None:
        """Wait until every pending acknowledgement has been processed (for tests)."""
        while pending := [t for t in self._tasks if not t.done()]:
            # only unfinished ones: gathering finished tasks never yields to the event loop,
            # so their clean-up callbacks would never run and this would spin forever
            await asyncio.gather(*pending, return_exceptions=True)

    def on_tick(self, tick: Tick) -> None:
        """Process one market update: trigger stops, then try to fill working orders."""
        self._check_gtts(tick)
        book = self._book_for(tick)
        working = [self._orders[b] for b in self._by_instrument.get(tick.instrument_id, ())]
        for order in sorted(working, key=lambda o: o.seq):
            if not order.accepted or order.status.is_final:
                continue
            if not order.triggered:
                if self._try_trigger(order, tick):
                    self._enter(order, book)  # a triggered stop arrives like a new order
                continue
            if order.queued_on is tick:
                continue  # this tick happened before we joined the queue
            if self._traded_through(order, tick):
                self._fill(order, order.leaves, order.limit_paise)
            elif self._is_marketable(order, tick):
                self._take_liquidity(order, book, at_own_price=True)
            else:
                self._passive_fill(order, tick, book)

    async def _run(self) -> None:
        while True:
            for tick in await self._subscription.next_batch():
                self.on_tick(tick)

    # --- order life --------------------------------------------------------------------

    async def _accept(self, order: _PaperOrder) -> None:
        await self._sleep(self._latency_s)
        if order.status.is_final:  # cancelled before the broker acknowledged it
            return
        tick = self._market_data.latest(order.instrument.id)
        if order.request.order_type is OrderType.MARKET:
            if tick is None:
                self._finish(order, OrderStatus.REJECTED, "no market price to protect against")
                return
            order.limit_paise = protected_limit(
                order.instrument,
                order.request.side,
                tick.ltp_paise,
                order.request.market_protection,
            )
        order.accepted = True
        order.status = OrderStatus.OPEN
        self._emit(order)
        if tick is None:
            return
        if not order.triggered and not self._try_trigger(order, tick):
            return
        self._enter(order, self._book_for(tick))

    async def _after_modify(self, order: _PaperOrder, loses_place: bool) -> None:
        await self._sleep(self._latency_s)
        if order.status.is_final or not order.accepted:
            return  # finished meanwhile, or acceptance will use the new values
        self._emit(order)
        tick = self._market_data.latest(order.instrument.id)
        if tick is None or not order.triggered:
            return
        book = self._book_for(tick)
        if self._is_marketable(order, tick):
            self._take_liquidity(order, book)  # a modify that crosses acts like a new order
        elif loses_place:
            self._join_queue(order, tick)

    async def _cancel(self, order: _PaperOrder, reason: str) -> None:
        await self._sleep(self._latency_s)
        if not order.status.is_final:
            self._finish(order, OrderStatus.CANCELLED, reason)

    def _book_for(self, tick: Tick) -> _Book:
        """The book for this tick. A tick seen for the first time resets the depth we've
        used and works out how much traded since the previous tick (volume is cumulative,
        so skipped ticks don't lose volume)."""
        book = self._books.setdefault(tick.instrument_id, _Book())
        if book.tick is tick:
            return book
        traded = 0
        if tick.volume is not None:
            previous = self._last_volume.get(tick.instrument_id)
            if previous is not None and tick.volume > previous:
                traded = tick.volume - previous
            if previous is None or tick.volume > previous:
                self._last_volume[tick.instrument_id] = tick.volume  # never move backwards
        book.tick, book.used = tick, {}
        book.traded_left = {tick.ltp_paise: traded} if traded else {}
        return book

    def _enter(self, order: _PaperOrder, book: _Book) -> None:
        """An order arriving at the exchange: trade what crosses, queue the rest."""
        if self._is_marketable(order, book.tick):
            self._take_liquidity(order, book)
        else:
            self._join_queue(order, book.tick)
        self._end_if_ioc(order)

    def _join_queue(self, order: _PaperOrder, tick: Tick) -> None:
        order.queue_ahead = _queue_at(order, tick)
        order.queued_on = tick

    def _traded_through(self, order: _PaperOrder, tick: Tick) -> bool:
        """Someone traded at a worse price than ours, so our better order must have filled."""
        if order.request.side is Side.BUY:
            return tick.ltp_paise < order.limit_paise
        return tick.ltp_paise > order.limit_paise

    def _try_trigger(self, order: _PaperOrder, tick: Tick) -> bool:
        trigger = order.request.trigger_paise
        hit = (
            tick.ltp_paise >= trigger
            if order.request.side is Side.BUY
            else tick.ltp_paise <= trigger
        )
        if not hit:
            return False
        order.triggered = True
        if order.request.order_type is OrderType.SL_M:
            order.limit_paise = protected_limit(
                order.instrument,
                order.request.side,
                tick.ltp_paise,
                order.request.market_protection,
            )
        else:
            order.limit_paise = order.request.price_paise
        return True

    def _is_marketable(self, order: _PaperOrder, tick: Tick) -> bool:
        if order.request.side is Side.BUY:
            best = tick.best_ask
            return best is not None and best.price_paise <= order.limit_paise
        best = tick.best_bid
        return best is not None and best.price_paise >= order.limit_paise

    def _take_liquidity(
        self, order: _PaperOrder, book: _Book, *, at_own_price: bool = False
    ) -> None:
        """Trade against visible depth. An incoming order trades at each level's price; a
        resting order that the market moved onto trades at its own price."""
        tick = book.tick
        buying = order.request.side is Side.BUY
        levels: tuple[DepthLevel, ...] = tick.asks if buying else tick.bids
        side_key = "ask" if buying else "bid"
        for level in levels:
            if order.leaves <= 0 or level.qty <= 0:
                continue
            crosses = (
                level.price_paise <= order.limit_paise
                if buying
                else (level.price_paise >= order.limit_paise)
            )
            if not crosses:
                break
            key = (side_key, level.price_paise)
            available = level.qty - book.used.get(key, 0)
            if available <= 0:
                continue
            qty = min(available, order.leaves)
            book.used[key] = book.used.get(key, 0) + qty
            self._fill(order, qty, order.limit_paise if at_own_price else level.price_paise)
        if not order.status.is_final:
            self._join_queue(order, tick)

    def _passive_fill(self, order: _PaperOrder, tick: Tick, book: _Book) -> None:
        """Volume traded at our price eats the queue ahead of us first, then fills us."""
        price = order.limit_paise
        if order.queue_ahead == UNKNOWN_QUEUE:
            self._join_queue(order, tick)  # first sight of our level: count from the next tick
            return
        pool = book.traded_left.get(price, 0)
        if pool <= 0:
            return
        eaten_ahead = min(pool, order.queue_ahead)
        order.queue_ahead -= eaten_ahead
        pool -= eaten_ahead
        qty = min(pool, order.leaves)
        book.traded_left[price] = pool - qty
        if qty > 0:
            self._fill(order, qty, price)

    def _end_if_ioc(self, order: _PaperOrder) -> None:
        if order.request.validity is Validity.IOC and not order.status.is_final:
            self._finish(order, OrderStatus.CANCELLED, "IOC: unfilled quantity cancelled")

    def _fill(self, order: _PaperOrder, qty: int, price: int) -> None:
        order.filled += qty
        order.notional += qty * price
        r = order.request
        self._trades.append(
            Trade(f"T{next(self._trade_ids)}", order.broker_id, r.instrument_id, r.side,
                  r.product, qty, price, self._clock(), r.client_order_id)
        )  # fmt: skip
        self._book.apply_fill(r.instrument_id, r.product, r.side, qty, price)
        order.status = OrderStatus.FILLED if order.leaves == 0 else OrderStatus.PARTIAL
        self._emit(order, last_fill_qty=qty, last_fill_price=price)

    def _finish(self, order: _PaperOrder, status: OrderStatus, reason: str) -> None:
        order.status = status
        self._emit(order, reason=reason)

    def _working(self, broker_order_id: str) -> _PaperOrder:
        order = self._orders.get(broker_order_id)
        if order is None:
            raise BrokerError(f"unknown order {broker_order_id}")
        if order.status.is_final:
            raise BrokerError(f"order {broker_order_id} is already {order.status.value.lower()}")
        return order

    def _snapshot(self, order: _PaperOrder, **extra) -> OrderUpdate:
        return OrderUpdate(
            client_order_id=order.request.client_order_id,
            broker_order_id=order.broker_id,
            status=order.status,
            filled_qty=order.filled,
            avg_price_paise=order.avg_price,
            **extra,
        )

    def _emit(self, order: _PaperOrder, *, last_fill_qty=0, last_fill_price=0, reason="") -> None:
        self._queue.put_nowait(
            self._snapshot(
                order,
                last_fill_qty=last_fill_qty,
                last_fill_price_paise=last_fill_price,
                reason=reason,
            )
        )

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _queue_at(order: _PaperOrder, tick: Tick) -> int:
    """Quantity ahead of a new resting order at its price, from visible depth."""
    buying = order.request.side is Side.BUY
    levels = tick.bids if buying else tick.asks
    visible = [lvl for lvl in levels if lvl.qty > 0]
    price = order.limit_paise
    for level in visible:
        if level.price_paise == price:
            return level.qty
    if not visible:
        return 0
    best = visible[0].price_paise
    improves = price > best if buying else price < best
    if improves:
        return 0  # we'd be the new best price: first in line
    worst = visible[-1].price_paise
    inside = price > worst if buying else price < worst
    return 0 if inside else UNKNOWN_QUEUE  # a gap inside the depth is empty; beyond it, unknown
