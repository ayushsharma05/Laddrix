"""The slicer: equal slices on a clock, kept at the best price, within the modify budget."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoState
from mocktrade.algos.slicer import MODIFY_BUDGET, slice_sizes
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


def book(bid=2501000, ask=2501010, ask_qty=75, volume=10_000):
    return Tick(
        FUT,
        bid,
        volume=volume,
        bids=(DepthLevel(bid, 150, 2),),
        asks=(DepthLevel(ask, ask_qty, 1),),
    )


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(book())
    broker = FakeBroker()
    clock = Clock()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    engine = AlgoEngine("acc-1", om, md, store.get, BUILTIN, clock=clock)

    class W:
        pass

    w = W()
    w.md, w.broker, w.om, w.engine, w.clock = md, broker, om, engine, clock
    return w


def slicer(w, qty=750, side=Side.BUY, **params):
    return w.engine.start(
        "SLICER",
        user_id="ayush",
        instrument_id=FUT,
        side=side,
        qty=qty,
        params={"slices": 10, "interval_s": 60, **params},
    )


async def advance(w, seconds):
    w.clock.now += timedelta(seconds=seconds)
    w.engine.heartbeat()
    await w.engine.settle()


def test_slices_are_whole_lots_as_even_as_possible():
    assert slice_sizes(10, 4) == [3, 3, 2, 2]
    assert slice_sizes(10, 10) == [1] * 10
    assert slice_sizes(3, 10) == [1, 1, 1]  # never an empty slice
    assert sum(slice_sizes(37, 6)) == 37


async def test_ten_slices_over_ten_minutes(world):
    algo = slicer(world)  # 750 = 10 lots of 75, one lot a minute
    await world.engine.settle()
    times = [world.clock.now]
    assert len(world.broker.placed) == 1  # the first slice goes at once
    for minute in range(1, 10):
        await advance(world, 30)
        assert len(world.broker.placed) == minute  # nothing between slices
        await advance(world, 30)
        assert len(world.broker.placed) == minute + 1
        times.append(world.clock.now)
    assert [r.qty for r in world.broker.placed] == [75] * 10
    assert all(r.price_paise == 2501000 for r in world.broker.placed)  # join: the best bid
    assert [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)] == [60.0] * 9
    await advance(world, 60)
    assert len(world.broker.placed) == 10  # never more than the parent
    assert world.engine.get(algo.algo_id).working_qty == 750


async def test_cross_takes_the_other_side_and_sells_mirror(world):
    slicer(world, qty=150, slices=2, mode="cross")
    slicer(world, qty=150, side=Side.SELL, slices=2, mode="cross")
    slicer(world, qty=150, side=Side.SELL, slices=2)  # join, selling: the best ask
    await world.engine.settle()
    assert [(r.side, r.price_paise) for r in world.broker.placed] == [
        (Side.BUY, 2501010),
        (Side.SELL, 2501000),
        (Side.SELL, 2501010),
    ]


async def test_never_beyond_the_limit_price(world):
    algo = slicer(world, qty=150, slices=2, mode="cross", limit_paise=2501000)
    await world.engine.settle()
    assert world.broker.placed == []  # the offer 25,010.10 is above the 25,010.00 limit
    assert "beyond the limit" in world.engine.get(algo.algo_id).status
    world.md.on_tick(book(bid=2500990, ask=2501000))
    await advance(world, 60)
    assert [r.price_paise for r in world.broker.placed] == [2501000]


async def test_an_unfilled_slice_follows_the_best_price_on_the_slice_clock(world):
    slicer(world, qty=150, slices=2)
    await world.engine.settle()
    world.md.on_tick(book(bid=2501020, ask=2501030))
    await advance(world, 10)  # between slices: no chasing on every tick
    assert world.broker.modified == []
    await advance(world, 50)
    first = world.om.get(world.om.orders()[0].client_order_id)
    assert world.broker.modified == [
        (first.broker_order_id, {"qty": None, "price_paise": 2501020, "trigger_paise": None})
    ]
    assert [r.price_paise for r in world.broker.placed] == [2501000, 2501020]


async def test_a_slice_is_replaced_before_it_uses_the_brokers_25_modifications(world):
    algo = slicer(world, qty=75, slices=1)
    await world.engine.settle()
    (first,) = world.engine.get(algo.algo_id).children
    broker_id = world.om.get(first).broker_order_id
    for n in range(1, MODIFY_BUDGET + 3):
        world.md.on_tick(book(bid=2501000 + 10 * n, ask=2501010 + 10 * n))
        await advance(world, 60)
        if world.om.get(first).pending.value == "CANCEL":
            world.om.apply(OrderUpdate(first, broker_id, OrderStatus.CANCELLED))
            await advance(world, 60)
            break
    mods = [m for m in world.broker.modified if m[0] == broker_id]
    assert len(mods) == 24  # one under the broker's 25, so never refused for it
    assert broker_id in world.broker.cancelled
    children = world.engine.get(algo.algo_id).children
    assert len(children) == 2  # its quantity went out again as a fresh order
    assert world.om.get(children[1]).request.qty == 75


async def test_a_refused_slice_is_retried_as_slices_never_one_big_order(world):
    algo = slicer(world, qty=300, slices=4)  # 4 slices of 75
    world.broker.fail_next("exchange busy")  # the first slice is refused
    await world.engine.settle()
    for _ in range(4):
        await advance(world, 60)
    sizes = [r.qty for r in world.broker.placed]
    assert sizes == [75, 75, 75, 75]  # three planned slices, then the refused one's lot again
    assert world.engine.get(algo.algo_id).working_qty == 300
    assert "catching up" in world.engine.get(algo.algo_id).status


async def test_resume_sends_the_pulled_slice_at_once_not_at_the_next_slice_time(world):
    algo = slicer(world, qty=150, slices=2)
    await world.engine.settle()
    world.engine.pause(algo.algo_id)
    await world.engine.settle()
    (first,) = world.engine.get(algo.algo_id).children
    world.om.apply(OrderUpdate(first, world.om.get(first).broker_order_id, OrderStatus.CANCELLED))
    await advance(world, 10)  # resumed 10 s later: the next slice wasn't due for 50 s more
    world.engine.resume(algo.algo_id)
    await advance(world, 0)
    assert len(world.broker.placed) == 2


async def test_paused_slices_are_pulled_and_resume_carries_on(world):
    algo = slicer(world, qty=150, slices=2)
    await world.engine.settle()
    world.engine.pause(algo.algo_id)
    await world.engine.settle()
    (first,) = world.engine.get(algo.algo_id).children
    world.om.apply(OrderUpdate(first, world.om.get(first).broker_order_id, OrderStatus.CANCELLED))
    await advance(world, 120)
    assert len(world.broker.placed) == 1  # nothing while paused
    world.engine.resume(algo.algo_id)
    await advance(world, 0)
    assert [r.qty for r in world.broker.placed] == [75, 75]  # the pulled slice's lot is back


async def test_a_750_parent_completes_against_the_paper_broker(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(book(ask_qty=1500))
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"),
        paper,
        ids=ClientIds("MT"),
        limits=RiskLimits(max_order_value_paise=1_00_00_000_00),  # ₹1 crore per slice
        algo_types=BUILTIN,
    )
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        algo = runtime.algos.start(
            "SLICER",
            user_id="ayush",
            instrument_id=FUT,
            side=Side.BUY,
            qty=750,
            params={"slices": 2, "interval_s": 1, "mode": "cross"},
        )
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.05)
        view = runtime.algos.get(algo.algo_id)
        assert (view.filled_qty, view.avg_price_paise) == (750, 2501010)
        assert [runtime.orders.get(c).request.qty for c in view.children] == [375, 375]
        assert runtime.positions.net_qty(FUT) == 750
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()
