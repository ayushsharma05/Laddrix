"""Coordinated exits (#261): closing orders are reduce-only, so no mix of exits can close more
than is open and reverse a position; forced closes (loss limits, square-off) go through one
exit desk per account that stops the algos on the position, cancels resting closing orders,
and closes only what is left; stop algos size their exit to what is still open."""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from test_mtm import Clock, flat, move, notes
from test_portfolio import FUT, Paper
from test_protect import price, start, view

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoState
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.codec import request_from_dict, request_to_dict
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds, OrderError, Pending
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.closing import CloseResult, ExitDesk
from mocktrade.risk.mtm import PositionRule

S = OrderStatus


@pytest.fixture
async def day(nifty_fut, tmp_path):
    clock = Clock()
    p = Paper(nifty_fut, tmp_path, clock=clock, algo_types=BUILTIN)
    p.clock = clock
    yield p
    await p.stop()


def sell(qty, *, price=None, reduce_only=True, product=Product.MIS):
    kind = OrderType.MARKET if price is None else OrderType.LIMIT
    protection = Decimal(-1) if price is None else None
    return OrderRequest("", "acc", "ayush", FUT, Side.SELL, qty, kind, product, price_paise=price,
                        market_protection=protection, reduce_only=reduce_only)  # fmt: skip


def closing_sold(p):
    """Quantity sold by closing orders (the desk's and the algos')."""
    return sum(o.filled_qty for o in p.rt.orders.orders() if o.request.side is Side.SELL)


# --- the risk gate: a reduce-only order closes what is open, no more ----------------------------


async def test_a_reduce_only_order_is_refused_when_it_would_reverse_the_position(day):
    p = day
    refused = await p.rt.orders.submit(sell(75))
    assert refused.status is S.REJECTED
    assert refused.reason == "nothing to close: no MIS position this order reduces"
    await p.buy(FUT, 150, 2501010, Product.MIS)
    too_many = await p.rt.orders.submit(sell(225))
    assert (
        too_many.reason == "would close more than is open: 150 open in MIS, 0 already being closed"
    )
    other_product = await p.rt.orders.submit(sell(75, product=Product.NRML))
    assert other_product.status is S.REJECTED  # an NRML close doesn't reduce the MIS position
    resting = await p.rt.orders.submit(sell(75, price=2510000))  # above the bid: it rests
    assert resting.status is not S.REJECTED, resting.reason
    second = await p.rt.orders.submit(sell(150, price=2510000))
    assert (
        second.reason == "would close more than is open: 150 open in MIS, 75 already being closed"
    )
    last = await p.rt.orders.submit(sell(75, price=2511000))  # not identical: no double click
    assert last.status is not S.REJECTED, last.reason  # exactly the rest


async def test_an_ordinary_sell_is_not_limited_it_may_open_a_short(day):
    p = day
    view = await p.rt.orders.submit(sell(75, reduce_only=False))
    async with asyncio.timeout(5):
        while p.rt.positions.net_qty(FUT) != -75:
            await asyncio.sleep(0.01)
    assert view.request.reduce_only is False


async def test_growing_a_reduce_only_order_past_the_position_is_refused(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    resting = await p.rt.orders.submit(sell(150, price=2510000))
    with pytest.raises(OrderError, match="would close more than is open: 150 open in MIS"):
        await p.rt.orders.modify(resting.client_order_id, qty=225)
    await p.rt.orders.modify(resting.client_order_id, qty=75)  # shrinking is always fine
    async with asyncio.timeout(5):
        while p.rt.orders.get(resting.client_order_id).request.qty != 75:
            await asyncio.sleep(0.01)


def test_reduce_only_survives_the_saved_order_round_trip():
    request = sell(75)
    assert request_from_dict(request_to_dict(request)).reduce_only is True
    old = request_to_dict(sell(75, reduce_only=False))
    old.pop("reduce_only", None)  # saved before #261
    assert request_from_dict(old).reduce_only is False


# --- the exit desk -------------------------------------------------------------------------------


async def test_square_off_cancels_a_resting_close_first_and_never_reverses(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    target = await p.rt.orders.submit(sell(75, price=2510000))  # the trader's own target
    p.clock.set(15, 5)
    (done,) = await p.rt.squareoff.close_all("ayush")
    await flat(p, product=Product.MIS)
    assert done["cancelled"] == [target.client_order_id]
    assert p.rt.orders.get(target.client_order_id).status is S.CANCELLED
    assert done["left_open"] == 0
    sent = [p.rt.orders.get(c) for c in done["orders"]]
    assert [(o.request.qty, o.request.reduce_only) for o in sent] == [(150, True)]
    await asyncio.sleep(0.1)
    assert p.rt.positions.net_qty(FUT) == 0  # flat, not short
    await p.log.flush()
    (line,) = notes(p, "SQUARE_OFF")
    assert f"cancelled {target.client_order_id}" in line


async def test_square_off_stops_the_stop_algo_on_that_position_first(day):
    p = day
    await p.buy(FUT, 75, 2501010, Product.MIS)
    algo = p.rt.algos.start("TRAILING_STOP", user_id="ayush", instrument_id=FUT,
                            side=Side.SELL, qty=75, params={"trail_paise": 2000},
                            product=Product.MIS)  # fmt: skip
    other = p.rt.algos.start("TRAILING_STOP", user_id="ayush", instrument_id=FUT,
                             side=Side.SELL, qty=75, params={"trail_paise": 2000},
                             product=Product.NRML)  # fmt: skip
    p.clock.set(15, 5)
    (done,) = await p.rt.squareoff.close_all("ayush")
    await flat(p, product=Product.MIS)
    assert done["stopped_algos"] == [algo.algo_id]
    await p.rt.algos.settle()
    stopped = p.rt.algos.get(algo.algo_id)
    assert stopped.state is AlgoState.STOPPED
    assert stopped.reason.startswith("forced close: square-off by ayush")
    assert not p.rt.algos.get(other.algo_id).state.is_final  # another product: untouched


async def test_a_loss_limit_and_a_square_off_at_once_close_the_position_once(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    p.rt.mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00,
                                            auto_close=True), "ayush")  # fmt: skip
    p.clock.set(15, 5)
    lows = []
    p.rt.positions.subscribe(lambda *_: lows.append(p.rt.positions.net_qty(FUT)))
    squareoff = asyncio.create_task(p.rt.squareoff.close_all("ayush"))
    await move(p, 2499000)  # the limit fires while the square-off runs
    await squareoff
    await p.rt.mtm.settle()
    await flat(p, product=Product.MIS)
    await asyncio.sleep(0.1)
    assert p.rt.positions.net_qty(FUT) == 0 and min(lows) == 0  # never short, not even briefly
    assert closing_sold(p) == 150
    refused = [o for o in p.rt.orders.orders() if o.status is S.REJECTED]
    assert refused == []  # the second close found nothing left: it sent nothing at all


@dataclass
class _View:
    client_order_id: str
    request: OrderRequest
    leaves_qty: int
    pending: Pending = Pending.NONE
    status: OrderStatus = S.OPEN


@dataclass
class _Orders:
    """Just enough of an order manager for the desk on its own."""

    account_id: str = "acc"
    rows: list = field(default_factory=list)
    cancels: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    refuse_cancel: str | None = None
    cancel_takes: bool = True

    def working(self):
        return [r for r in self.rows if r.leaves_qty > 0]

    async def cancel(self, cid):
        self.cancels.append(cid)
        if self.refuse_cancel:
            raise OrderError(self.refuse_cancel)
        if self.cancel_takes:
            self.rows = [r for r in self.rows if r.client_order_id != cid]

    async def submit(self, request):
        self.sent.append(request)
        return _View(f"X{len(self.sent)}", request, request.qty)


async def _no_wait(_):
    pass


async def test_an_order_whose_fate_is_unknown_is_left_alone_and_its_part_not_closed_twice(
    nifty_fut,
):
    orders = _Orders(rows=[_View("U1", sell(75), 75, Pending.UNCONFIRMED)])
    desk = ExitDesk(orders, lambda i, p: 150, sleep=_no_wait)
    result = await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=10,
                              basket_id="B", why="test")  # fmt: skip
    assert orders.cancels == []  # can't cancel what the broker hasn't named
    assert [(r.side, r.qty, r.reduce_only) for r in orders.sent] == [(Side.SELL, 75, True)]
    assert (result.position, result.left_open) == (150, 75)
    assert result.words() == ("closing +150 NFO:NIFTY26OCTFUT MIS: sent X1; "
                              "75 left to orders already closing it")  # fmt: skip


async def test_a_cancel_that_does_not_take_in_time_keeps_its_part(nifty_fut):
    orders = _Orders(rows=[_View("R1", sell(50, price=2510000), 50)], cancel_takes=False)
    slept = []

    async def sleep(s):
        slept.append(s)

    desk = ExitDesk(orders, lambda i, p: 75, sleep=sleep, settle_s=0.2)
    result = await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=10,
                              basket_id="B", why="test")  # fmt: skip
    assert orders.cancels == ["R1"] and result.cancelled == ["R1"]
    assert round(sum(slept), 2) == 0.2  # waited, then gave up waiting
    assert [r.qty for r in orders.sent] == [25] and result.left_open == 50


async def test_a_refused_cancel_is_reported_and_still_counted(nifty_fut):
    orders = _Orders(rows=[_View("R1", sell(50, price=2510000), 50)], refuse_cancel="filled")
    desk = ExitDesk(orders, lambda i, p: 75, sleep=_no_wait)
    result = await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=10,
                              basket_id="B", why="test")  # fmt: skip
    assert result.cancelled == [] and result.notes == ["couldn't cancel R1: filled"]
    assert [r.qty for r in orders.sent] == [25]
    assert "couldn't cancel R1: filled" in result.words()


async def test_buys_and_other_products_are_not_closing_orders(nifty_fut):
    buy = OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT, Product.MIS,
                       price_paise=2400000)  # fmt: skip
    orders = _Orders(rows=[_View("B1", buy, 75), _View("N1", sell(75, product=Product.NRML), 75)])
    desk = ExitDesk(orders, lambda i, p: 75, sleep=_no_wait)
    result = await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=10,
                              basket_id="B", why="test")  # fmt: skip
    assert orders.cancels == [] and [r.qty for r in orders.sent] == [75]
    assert result.left_open == 0


async def test_a_short_is_closed_with_buys_and_nothing_open_sends_nothing(nifty_fut):
    orders = _Orders()
    held = {"q": -150}
    desk = ExitDesk(orders, lambda i, p: held["q"], sleep=_no_wait)
    await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=1, basket_id="B", why="t")
    assert [(r.side, r.qty) for r in orders.sent] == [(Side.BUY, 75), (Side.BUY, 75)]
    held["q"] = 0
    flat_result = await desk.close(nifty_fut, Product.MIS, user_id="mtm", max_lots=1,
                                   basket_id="B", why="t")  # fmt: skip
    assert len(orders.sent) == 2 and flat_result.words().endswith(": nothing left to close")


async def test_two_closes_of_one_position_take_turns(nifty_fut):
    orders = _Orders()
    held = {"q": 75}
    inside, most = [0], [0]
    real = orders.submit

    async def submit(request):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        await asyncio.sleep(0.01)
        held["q"] -= request.qty  # it filled
        inside[0] -= 1
        return await real(request)

    orders.submit = submit
    desk = ExitDesk(orders, lambda i, p: held["q"], sleep=_no_wait)
    both = await asyncio.gather(
        *(
            desk.close(nifty_fut, Product.MIS, user_id=u, max_lots=10, basket_id="B", why="t")
            for u in ("mtm", "ayush")
        )
    )
    assert most[0] == 1 and held["q"] == 0
    assert [len(r.sent) for r in both] == [1, 0]  # the second saw nothing left


def test_close_result_lists_what_it_did():
    r = CloseResult(FUT, Product.MIS, position=75, stopped_algos=["A1"], cancelled=["R1"])
    assert r.words() == f"closing +75 {FUT} MIS: stopped algos A1; cancelled R1"


# --- stop algos size their exit to what is still open -------------------------------------------


@pytest.fixture
def w(nifty_fut):
    from test_protect import tick

    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(tick(2501000))
    broker = FakeBroker()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"))
    held = {"q": 150}
    engine = AlgoEngine("acc-1", om, md, store.get, BUILTIN,
                        position=lambda i, p: held["q"])  # fmt: skip

    class W:
        pass

    x = W()
    x.md, x.broker, x.om, x.engine, x.held = md, broker, om, engine, held
    return x


async def test_a_stop_exits_only_what_is_still_open(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000)
    await w.engine.settle()
    w.held["q"] = 75  # someone closed half meanwhile
    await price(w, 2499000)
    (exit_,) = w.broker.placed
    assert (exit_.qty, exit_.reduce_only) == (75, True)
    assert "exiting 75" in view(w, algo).status or view(w, algo).working_qty == 75


async def test_a_stop_whose_position_is_already_closed_finishes_and_says_so(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000)
    await w.engine.settle()
    w.held["q"] = 0
    await price(w, 2499000)
    await w.engine.settle()
    assert w.broker.placed == []
    done = view(w, algo)
    assert done.state is AlgoState.STOPPED
    assert done.reason == "the position is already closed (another exit got there first)"


async def test_an_oco_stop_exits_only_what_is_still_open(w):
    algo = start(w, "OCO", target_paise=2520000, stop_paise=2490000)
    await w.engine.settle()
    (target,) = w.broker.placed
    assert target.reduce_only is True
    w.held["q"] = 75
    await price(w, 2489000)
    await w.engine.settle()
    from test_protect import cancelled, kids

    cancelled(w, kids(w, algo)[0])  # the target comes off first
    await w.engine.settle()
    assert [(o.qty, o.reduce_only) for o in w.broker.placed[1:]] == [(75, True)]


async def test_a_bracket_entry_is_never_reduce_only_its_exits_are(w):
    from test_protect import fill, kids

    w.held["q"] = 0
    algo = start(w, "BRACKET", side=Side.BUY, qty=75, entry_paise=2501000,
                 target_points_paise=3000, stop_points_paise=2000)  # fmt: skip
    await w.engine.settle()
    (entry,) = w.broker.placed
    assert entry.reduce_only is False
    w.held["q"] = 75
    fill(w, kids(w, algo)[0])
    await w.engine.settle()
    assert [(o.side, o.qty, o.reduce_only) for o in w.broker.placed[1:]] == [(Side.SELL, 75, True)]


async def test_a_reduce_only_order_on_the_side_that_adds_is_refused(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    buy = OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75, OrderType.MARKET, Product.MIS,
                       market_protection=Decimal(-1), reduce_only=True)  # fmt: skip
    added = await p.rt.orders.submit(buy)  # a "close" that would add to a long
    assert added.reason == "nothing to close: no MIS position this order reduces"


async def test_the_traders_own_plain_orders_are_not_counted_as_closing(day):
    """A plain sell is the trader's own: it may be meant to go short, so the gate doesn't count
    it against closes (the exit desk cancels it before a forced close)."""
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    plain = await p.rt.orders.submit(sell(75, price=2510000, reduce_only=False))
    assert plain.status is not S.REJECTED
    close = await p.rt.orders.submit(sell(150, price=2511000))
    assert close.status is not S.REJECTED, close.reason


async def test_a_stop_protecting_a_long_does_nothing_to_a_short(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000)  # SELL exit: protects a long
    await w.engine.settle()
    w.held["q"] = -150  # the account is short instead
    await price(w, 2499000)
    await w.engine.settle()
    assert w.broker.placed == [] and view(w, algo).state is AlgoState.STOPPED


async def test_an_algos_own_closing_orders_are_not_counted_against_it(w):
    from mocktrade.algos.engine import AlgoContext

    algo = start(w, "OCO", target_paise=2520000, stop_paise=2490000)
    await w.engine.settle()
    (target,) = w.broker.placed  # its own reduce-only target, working
    ctx = AlgoContext(w.engine, w.engine._parents[algo.algo_id])
    assert ctx.closable(Side.SELL) == 150  # its own target doesn't take from it
    other = start(w, "OCO", target_paise=2530000, stop_paise=2480000)
    await w.engine.settle()
    assert ctx.closable(Side.SELL) == 0  # but another algo's does
    assert other.algo_id != algo.algo_id
