"""Execution algos: iceberg, TWAP, VWAP and POV on a fake clock against a fake broker."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoError, AlgoState, AlgoType
from mocktrade.algos.iceberg import SPEC as ICEBERG
from mocktrade.algos.iceberg import Iceberg
from mocktrade.algos.pov import SPEC as POV
from mocktrade.algos.pov import Pov
from mocktrade.algos.schedule import (
    TWAP_SPEC,
    VWAP_SPEC,
    Twap,
    VolumeCurve,
    Vwap,
    market_close,
    nse_u_shape,
)
from mocktrade.algos.spec import spec_to_dict
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.options.model import IST
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.refdata.store import InstrumentStore

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)  # a Wednesday, 09:30 IST
BID, ASK = 2501000, 2501010


class Still(random.Random):
    """No randomness: every jitter and variation is zero."""

    def uniform(self, a, b):
        return 0.0


def book(bid=BID, ask=ASK, volume=10_000, avg=None):
    return Tick(
        FUT,
        bid,
        volume=volume,
        avg_price_paise=avg,
        bids=(DepthLevel(bid, 150, 2),),
        asks=(DepthLevel(ask, 150, 1),),
    )


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def types(rng=None):
    rng = rng or Still()
    return (
        AlgoType(ICEBERG, lambda p: Iceberg(p, rng=rng)),
        AlgoType(TWAP_SPEC, lambda p: Twap(p, rng=rng)),
        AlgoType(VWAP_SPEC, lambda p: Vwap(p, rng=rng)),
        AlgoType(POV, Pov),
    )


def make_world(nifty_fut, now=T0, rng=None):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(book())
    broker = FakeBroker()
    clock = Clock(now)
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    engine = AlgoEngine("acc-1", om, md, store.get, types(rng), clock=clock)

    class W:
        pass

    w = W()
    w.md, w.broker, w.om, w.engine, w.clock = md, broker, om, engine, clock
    return w


@pytest.fixture
def world(nifty_fut):
    return make_world(nifty_fut)


def start(w, code, qty=750, side=Side.BUY, **params):
    return w.engine.start(
        code, user_id="ayush", instrument_id=FUT, side=side, qty=qty, params=params
    )


async def advance(w, seconds):
    w.clock.now += timedelta(seconds=seconds)
    w.engine.heartbeat()
    await w.engine.settle()


def children(w, algo):
    return [w.om.get(c) for c in w.engine.get(algo.algo_id).children]


def fill(w, child, qty=None, price=None):
    qty = qty if qty is not None else child.request.qty
    price = price or child.request.price_paise
    total = child.filled_qty + qty
    status = OrderStatus.FILLED if total >= child.request.qty else OrderStatus.PARTIAL
    w.om.apply(
        OrderUpdate(
            child.client_order_id, child.broker_order_id, status, total, price,
            last_fill_qty=qty, last_fill_price_paise=price,
        )
    )  # fmt: skip


async def fill_working(w, algo, price=None):
    for c in children(w, algo):
        if not c.status.is_final:
            fill(w, c, price=price)
    await w.engine.settle()


# --- the specs ---


def test_every_execution_algo_is_published_for_the_screens():
    codes = [t.spec.code for t in BUILTIN]
    assert {"TWAP", "VWAP", "POV", "ICEBERG"} <= set(codes)
    twap = spec_to_dict(TWAP_SPEC)
    assert twap["family"] == "execution"
    kinds = {p["name"]: p["kind"] for p in twap["params"]}
    assert kinds["duration_min"] == "minutes" and kinds["at_end"] == "choice"


def test_settings_are_checked_against_the_spec(world):
    with pytest.raises(AlgoError, match="Limit is required"):
        start(world, "ICEBERG", display_lots=2)
    with pytest.raises(AlgoError, match="Over \\(min\\) must be at most 375"):
        start(world, "TWAP", duration_min=400)
    with pytest.raises(AlgoError, match="Share must be at most 50"):
        start(world, "POV", pct=60, max_lots=2)


# --- iceberg ---


async def test_an_iceberg_shows_one_slice_at_a_time_and_refills_on_each_fill(world):
    algo = start(world, "ICEBERG", qty=750, display_lots=3, limit_paise=2500500)
    await world.engine.settle()
    shown = []
    for _ in range(6):
        working = [c for c in children(world, algo) if not c.status.is_final]
        if not working:
            break
        assert len(working) == 1  # never more than one slice in the market
        shown.append((working[0].request.qty, working[0].request.price_paise))
        fill(world, working[0])
        await world.engine.settle()
    assert shown == [(225, 2500500), (225, 2500500), (225, 2500500), (75, 2500500)]
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.DONE and view.filled_qty == 750
    assert view.extra["slices_shown"] == 4


async def test_iceberg_sizes_vary_in_whole_lots_but_add_up(nifty_fut):
    w = make_world(nifty_fut, rng=random.Random(7))
    algo = start(w, "ICEBERG", qty=75 * 40, display_lots=4, limit_paise=2500500, variance_pct=50)
    await w.engine.settle()
    sizes = []
    while any(not c.status.is_final for c in children(w, algo)):
        (c,) = [c for c in children(w, algo) if not c.status.is_final]
        sizes.append(c.request.qty // 75)
        fill(w, c)
        await w.engine.settle()
    assert sum(sizes) == 40
    assert all(1 <= s <= 6 for s in sizes)  # 4 ± 2
    assert len(set(sizes)) > 1  # not one fixed size


async def test_a_refused_iceberg_slice_is_shown_again_on_the_next_heartbeat(world):
    world.broker.fail_next("exchange busy")
    algo = start(world, "ICEBERG", qty=150, display_lots=1, limit_paise=2500500)
    await world.engine.settle()
    assert [c.status for c in children(world, algo)] == [OrderStatus.REJECTED]
    await advance(world, 0.1)
    assert [c.status for c in children(world, algo)][0] is OrderStatus.REJECTED
    assert len(children(world, algo)) == 2 and len(world.broker.placed) == 1


async def test_a_partly_filled_slice_waits_for_the_rest_before_the_next(world):
    algo = start(world, "ICEBERG", qty=300, display_lots=2, limit_paise=2500500)
    await world.engine.settle()
    (c,) = children(world, algo)
    fill(world, c, qty=75)
    await advance(world, 1)
    assert len(world.broker.placed) == 1


# --- TWAP ---


async def test_twap_rests_what_is_due_each_interval(world):
    algo = start(world, "TWAP", qty=750, duration_min=10, interval_s=60)
    await world.engine.settle()
    sent = []
    for _ in range(10):
        await advance(world, 60)
        new = [c for c in children(world, algo) if not c.status.is_final]
        sent.append([(c.request.qty, c.request.price_paise) for c in new])
        await fill_working(world, algo)
    # one lot a minute at our side's best price; the last is due as the window ends: crossed
    assert sent == [[(75, BID)]] * 9 + [[(75, ASK)]]
    assert world.engine.get(algo.algo_id).state is AlgoState.DONE


async def test_twap_crosses_the_spread_when_too_far_behind(world):
    algo = start(world, "TWAP", qty=750, duration_min=10, interval_s=60, max_behind_pct=10)
    await world.engine.settle()
    await advance(world, 60)  # due 1 lot: rests at the bid, unfilled
    assert [c.request.price_paise for c in children(world, algo)] == [BID]
    await advance(world, 60)  # due 2, filled 0: 20% behind, more than the 10% allowed
    assert [r.price_paise for r in world.broker.placed] == [BID, ASK]
    assert world.broker.modified[-1][1]["price_paise"] == ASK  # the resting one moves too
    assert "crossing to catch up" in world.engine.get(algo.algo_id).status


async def test_twap_window_end_crosses_the_rest_or_leaves_it(world):
    crossing = start(world, "TWAP", qty=300, duration_min=2, interval_s=60)
    leaving = start(world, "TWAP", qty=300, duration_min=2, interval_s=60, at_end="leave")
    await world.engine.settle()
    await advance(world, 60)
    await advance(world, 60)  # the window is over, nothing filled
    crossing_sent = sum(c.request.qty for c in children(world, crossing))
    assert crossing_sent == 300
    assert all(c.request.price_paise == ASK for c in children(world, crossing))
    await world.engine.settle()
    left = world.engine.get(leaving.algo_id)
    assert left.state in (AlgoState.STOPPING, AlgoState.STOPPED)
    assert "4 lots left unfilled" in left.reason


async def test_twap_respects_the_limit_even_at_the_end(world):
    algo = start(world, "TWAP", qty=150, duration_min=1, interval_s=60, limit_paise=BID)
    await world.engine.settle()
    await advance(world, 60)
    assert world.broker.placed == []  # crossing would pay the 25,010.10 offer
    assert "beyond the limit" in world.engine.get(algo.algo_id).status


async def test_a_seller_never_sells_below_the_limit(world):
    algo = start(world, "TWAP", qty=150, side=Side.SELL, duration_min=1, interval_s=60,
                 limit_paise=ASK)  # fmt: skip
    await world.engine.settle()
    await advance(world, 60)  # the end: crossing would sell at the 25,010.00 bid
    assert world.broker.placed == []
    assert "beyond the limit" in world.engine.get(algo.algo_id).status


async def test_a_window_past_the_close_is_cut_at_1530(nifty_fut):
    w = make_world(nifty_fut, now=datetime(2026, 10, 14, 15, 20, tzinfo=IST))
    algo = start(w, "TWAP", qty=750, duration_min=60, interval_s=60)
    await w.engine.settle()
    assert w.engine.get(algo.algo_id).extra["ends_at"].startswith("2026-10-14T15:30")
    await advance(w, 60)
    assert "cut at the 15:30 close" in w.engine.get(algo.algo_id).status
    assert market_close(datetime(2026, 10, 14, 16, 0, tzinfo=IST)) is None  # after hours
    assert market_close(datetime(2026, 10, 17, 10, 0, tzinfo=IST)) is None  # a Saturday


async def test_decision_times_move_by_up_to_a_fifth(nifty_fut):
    w = make_world(nifty_fut, rng=random.Random(3))
    algo = start(w, "TWAP", qty=75 * 50, duration_min=60, interval_s=30)
    await w.engine.settle()
    strategy = w.engine._parents[algo.algo_id].strategy
    gaps = set()
    for _ in range(600):  # 10 minutes of heartbeats
        before = strategy.next_at
        await advance(w, 1)
        if strategy.next_at != before:  # it decided: the next decision is set
            gaps.add(round((strategy.next_at - w.clock.now).total_seconds(), 3))
    assert len(gaps) > 10 and all(24 <= g <= 36 for g in gaps)  # 30 s ± 20%


# --- VWAP ---


def test_the_volume_curve_is_busy_at_the_open_and_close():
    curve = nse_u_shape()
    day = datetime(2026, 10, 14, tzinfo=IST)
    at = lambda h, m: day.replace(hour=h, minute=m)  # noqa: E731
    first = curve.between(at(9, 15), at(9, 45))
    lunch = curve.between(at(12, 30), at(13, 0))
    last = curve.between(at(15, 0), at(15, 30))
    assert first > 2 * lunch and last > 1.5 * lunch
    assert curve.upto(at(9, 0)) == 0 and curve.upto(at(16, 0)) == curve.cum[-1]
    half = curve.upto(at(9, 15).replace(second=30))
    assert half == pytest.approx(curve.cum[1] / 2)
    with pytest.raises(ValueError):
        VolumeCurve([0, 0])


async def test_vwap_front_loads_a_window_that_starts_at_the_open(nifty_fut):
    w = make_world(nifty_fut, now=datetime(2026, 10, 14, 9, 15, tzinfo=IST))
    algo = start(w, "VWAP", qty=75 * 100, duration_min=60, interval_s=60)
    await w.engine.settle()
    for _ in range(30):
        await advance(w, 60)
        await fill_working(w, algo)
    # more than half in the first half hour: the open is busy
    assert w.engine.get(algo.algo_id).filled_qty > 75 * 55
    twap = make_world(nifty_fut, now=datetime(2026, 10, 14, 9, 15, tzinfo=IST))
    even = start(twap, "TWAP", qty=75 * 100, duration_min=60, interval_s=60)
    await twap.engine.settle()
    for _ in range(30):
        await advance(twap, 60)
        await fill_working(twap, even)
    assert twap.engine.get(even.algo_id).filled_qty == 75 * 50


async def test_vwap_outside_market_hours_follows_time_and_says_so(nifty_fut):
    w = make_world(nifty_fut, now=datetime(2026, 10, 14, 18, 0, tzinfo=IST))
    algo = start(w, "VWAP", qty=750, duration_min=10, interval_s=60)
    await w.engine.settle()
    await advance(w, 60)
    view = w.engine.get(algo.algo_id)
    assert "following time, not volume" in view.status
    assert view.extra["schedule_pct"] == 10.0


async def test_vwap_reports_the_markets_vwap_and_how_we_did(world):
    world.md.on_tick(book(volume=1000, avg=2500000))
    algo = start(world, "VWAP", qty=150, duration_min=10, interval_s=60)
    await world.engine.settle()
    await advance(world, 60)
    (c,) = children(world, algo)
    fill(world, c, qty=75, price=2503500)
    world.md.on_tick(book(volume=2000, avg=2500500))  # the market traded 1000 at 25,010.00
    await advance(world, 60)
    extra = world.engine.get(algo.algo_id).extra
    assert extra["market_vwap_paise"] == 2501000
    assert extra["vs_vwap_bps"] == 10.0  # we paid 25 rupees more: 10 bps worse


async def test_a_seller_beating_the_vwap_shows_a_negative_number(world):
    world.md.on_tick(book(volume=1000, avg=2500000))
    algo = start(world, "VWAP", qty=150, side=Side.SELL, duration_min=10, interval_s=60)
    await world.engine.settle()
    await advance(world, 60)
    (c,) = children(world, algo)
    fill(world, c, qty=75, price=2503500)  # sold 25 rupees above the market's VWAP
    world.md.on_tick(book(volume=2000, avg=2500500))
    await advance(world, 60)
    assert world.engine.get(algo.algo_id).extra["vs_vwap_bps"] == -10.0


# --- POV ---


async def test_pov_trades_its_share_of_the_market_volume(world):
    algo = start(world, "POV", qty=750, pct=10, max_lots=5)
    await world.engine.settle()
    assert world.broker.placed == []  # nothing has traded yet
    world.md.on_tick(book(volume=10_000 + 1500))  # 10% of 1,500 = 150 = 2 lots
    await advance(world, 2)
    assert [(r.qty, r.price_paise) for r in world.broker.placed] == [(150, BID)]
    await advance(world, 2)  # still resting: what's working counts, nothing more is sent
    assert len(world.broker.placed) == 1
    await fill_working(world, algo)
    world.md.on_tick(book(volume=10_000 + 1500 + 600))  # 210 due, 150 done: under a lot
    await advance(world, 2)
    assert len(world.broker.placed) == 1
    world.md.on_tick(book(volume=10_000 + 9000))  # 900 due, capped at 5 lots a child
    await advance(world, 2)
    assert world.broker.placed[-1].qty == 375
    assert world.engine.get(algo.algo_id).extra["market_qty"] == 9000


async def test_a_resting_child_is_replaced_before_the_brokers_25_modifications(world):
    algo = start(world, "POV", qty=750, pct=10, max_lots=2)
    world.md.on_tick(book(volume=10_000 + 1500))
    await advance(world, 2)
    (child,) = children(world, algo)
    for n in range(1, 30):  # the bid moves before every decision
        world.md.on_tick(book(bid=BID + 10 * n, ask=ASK + 10 * n, volume=11_500))
        await advance(world, 2)
    mods = [m for m in world.broker.modified if m[0] == child.broker_order_id]
    assert len(mods) == 24 and child.broker_order_id in world.broker.cancelled


async def test_pov_waits_for_a_feed_with_volume_and_can_cross(world):
    world.md.on_tick(Tick(FUT, BID, bids=(DepthLevel(BID, 1, 1),), asks=(DepthLevel(ASK, 1, 1),)))
    algo = start(world, "POV", qty=750, pct=20, max_lots=10, mode="cross", side=Side.SELL)
    await world.engine.settle()
    await advance(world, 2)
    assert "no volume" in world.engine.get(algo.algo_id).status
    world.md.on_tick(book(volume=5000))
    await advance(world, 2)  # the baseline is set now
    world.md.on_tick(book(volume=5000 + 750))
    await advance(world, 2)
    assert [(r.side, r.qty, r.price_paise) for r in world.broker.placed] == [(Side.SELL, 150, BID)]


# --- the whole way, against the paper broker ---


@pytest.mark.parametrize(
    "code, params",
    [
        ("ICEBERG", {"display_lots": 2, "limit_paise": ASK}),
        ("POV", {"pct": 50, "max_lots": 4, "mode": "cross"}),
    ],
)
async def test_each_algo_completes_against_the_paper_broker(nifty_fut, code, params):
    import asyncio

    from mocktrade.broker.paper import PaperBroker
    from mocktrade.domain.accounts import BrokerAccount
    from mocktrade.platform import Platform
    from mocktrade.risk.kill_switch import KillSwitch
    from mocktrade.risk.limits import RiskLimits

    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(Tick(FUT, BID, volume=10_000, bids=(DepthLevel(BID, 1500, 2),),
                    asks=(DepthLevel(ASK, 1500, 1),)))  # fmt: skip
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"),
        paper,
        ids=ClientIds("MT"),
        limits=RiskLimits(max_order_value_paise=1_00_00_000_00),
        algo_types=BUILTIN,
    )
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        algo = runtime.algos.start(
            code, user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=450, params=params
        )
        volume = 10_000
        async with asyncio.timeout(10):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                volume += 300  # the market keeps trading (POV needs it; refreshes the book)
                md.on_tick(Tick(FUT, BID, volume=volume, bids=(DepthLevel(BID, 1500, 2),),
                                asks=(DepthLevel(ASK, 1500, 1),)))  # fmt: skip
                await asyncio.sleep(0.05)
        view = runtime.algos.get(algo.algo_id)
        assert view.filled_qty == 450 and runtime.positions.net_qty(FUT) == 450
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()
