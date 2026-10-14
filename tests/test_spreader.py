"""The spreader: quote leg A at the level, hedge leg B at once within the pay-up."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoState
from mocktrade.broker.base import BrokerError
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits

OCT, NOV = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


def quote(instrument_id, bid, ask, qty=750, volume=10_000):
    return Tick(
        instrument_id,
        bid,
        volume=volume,
        bids=(DepthLevel(bid, qty, 3),),
        asks=(DepthLevel(ask, qty, 3),),
    )


@pytest.fixture
def nov(nifty_fut):
    return replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24))


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def world(nifty_fut, nov):
    store = InstrumentStore()
    store.add(nifty_fut)
    store.add(nov)
    md = MarketData()
    md.on_tick(quote(OCT, 2501000, 2501010))
    md.on_tick(quote(NOV, 2514000, 2514010))  # NOV trades ~₹130 over OCT
    broker = FakeBroker()
    clock = Clock()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    engine = AlgoEngine("acc-1", om, md, store.get, BUILTIN, clock=clock)

    class W:
        pass

    w = W()
    w.store, w.md, w.broker, w.om, w.engine, w.clock = store, md, broker, om, engine, clock
    return w


def spread(w, level=-13000, side=Side.BUY, qty=150, payup=2, leg2=NOV):
    """Buy (or sell) the OCT − NOV spread at `level` paise (−₹130.00 by default)."""
    return w.engine.start(
        "SPREAD",
        user_id="ayush",
        instrument_id=OCT,
        side=side,
        qty=qty,
        params={"leg2": leg2, "level_paise": level, "payup_ticks": payup},
    )


def fill(w, child_id, qty, price):
    view = w.om.get(child_id)
    total = view.filled_qty + qty
    status = OrderStatus.FILLED if total >= view.request.qty else OrderStatus.PARTIAL
    w.om.apply(
        OrderUpdate(
            child_id, view.broker_order_id, status, total, price,
            last_fill_qty=qty, last_fill_price_paise=price,
        )
    )  # fmt: skip


def legs(w):
    placed = w.broker.placed
    return [(r.instrument_id[4:], r.side.value, r.qty, r.price_paise) for r in placed]


async def test_quotes_leg_a_where_the_spread_equals_the_level(world):
    spread(world)  # buy OCT − NOV at −130.00: OCT at NOV's bid 25,140.00 − 130.00
    await world.engine.settle()
    assert legs(world) == [("NIFTY26OCTFUT", "BUY", 150, 2501000)]
    spread(world, side=Side.SELL, level=-12990)  # sell: OCT at NOV's ask 25,140.10 − 129.90
    await world.engine.settle()
    assert legs(world)[1] == ("NIFTY26OCTFUT", "SELL", 150, 2501020)


async def test_rounds_so_the_spread_is_never_worse_than_the_level(world):
    spread(world, level=-13005)  # 25,140.00 − 130.05 = 25,009.95: a buy rounds down
    spread(world, side=Side.SELL, level=-12995)  # 25,140.10 − 129.95 = 25,010.15: a sell rounds up
    await world.engine.settle()
    assert [p for *_, p in legs(world)] == [2500990, 2501020]


async def test_requotes_when_leg_b_moves_and_not_otherwise(world):
    spread(world)
    await world.engine.settle()
    world.engine.on_tick(quote(OCT, 2501010, 2501020))  # leg A moving changes nothing
    await world.engine.settle()
    assert world.broker.modified == []
    world.md.on_tick(quote(NOV, 2514020, 2514030))
    world.engine.on_tick(world.md.latest(NOV))
    await world.engine.settle()
    assert [m[1]["price_paise"] for m in world.broker.modified] == [2501020]


async def test_the_hedge_goes_out_right_after_the_quote_fills_within_the_payup(world):
    algo = spread(world, payup=2)
    await world.engine.settle()
    (q,) = world.engine.get(algo.algo_id).children
    fill(world, q, 150, 2501000)
    await world.engine.settle()
    # sell NOV at its bid 25,140.00 minus 2 ticks of pay-up: never worse than 25,139.80
    assert legs(world)[1] == ("NIFTY26NOVFUT", "SELL", 150, 2513980)
    view = world.engine.get(algo.algo_id)
    assert view.extra["unhedged"] == 150 and view.state is AlgoState.WORKING
    (_, hedge) = view.children
    world.clock.now += timedelta(milliseconds=180)
    fill(world, hedge, 150, 2514000)
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.DONE  # done only when both legs are complete
    assert view.extra["traded_spread_paise"] == -13000
    assert view.extra["unhedged"] == 0 and view.extra["max_legging_ms"] == 180


async def test_partial_fills_are_hedged_as_they_come(world):
    algo = spread(world, qty=300)
    await world.engine.settle()
    (q,) = world.engine.get(algo.algo_id).children
    fill(world, q, 75, 2501000)
    await world.engine.settle()
    fill(world, q, 150, 2501000)
    await world.engine.settle()
    hedges = [r for r in world.broker.placed if r.instrument_id == NOV]
    assert [h.qty for h in hedges] == [75, 150]  # never more than what A filled
    world.engine.heartbeat()  # the timer doesn't double-hedge
    await world.engine.settle()
    assert len([r for r in world.broker.placed if r.instrument_id == NOV]) == 2


async def test_stop_pulls_the_quote_keeps_the_hedge_and_alerts_if_left_unhedged(world):
    algo = spread(world, qty=150)
    await world.engine.settle()
    (q,) = world.engine.get(algo.algo_id).children
    fill(world, q, 75, 2501000)  # part filled: a hedge for 75 goes out
    await world.engine.settle()
    q_broker = world.om.get(q).broker_order_id
    world.engine.stop(algo.algo_id, "done for the day")
    await world.engine.settle()
    assert world.broker.cancelled == [q_broker]  # the quote, not the hedge
    world.om.apply(OrderUpdate(q, q_broker, OrderStatus.CANCELLED, 75, 2501000))
    (_, hedge) = world.engine.get(algo.algo_id).children
    assert world.engine.get(algo.algo_id).state is AlgoState.STOPPING  # the hedge still works
    # the hedge is cancelled outside the algo: a stopping spreader sends it again (it never
    # wants to be left unhedged) ...
    world.om.apply(OrderUpdate(hedge, world.om.get(hedge).broker_order_id, OrderStatus.CANCELLED))
    await world.engine.settle()
    hedges = [r for r in world.broker.placed if r.instrument_id == NOV]
    assert [h.qty for h in hedges] == [75, 75]

    # ... until the broker refuses every hedge (e.g. under a kill switch): then it ends
    async def refuse(request):
        raise BrokerError("trading is stopped")

    world.broker.place = refuse
    newest = world.engine.get(algo.algo_id).children[-1]
    world.om.apply(OrderUpdate(newest, world.om.get(newest).broker_order_id, OrderStatus.CANCELLED))
    await world.engine.settle()
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.FAILED
    assert "UNHEDGED: 75 on NFO:NIFTY26OCTFUT" in view.reason
    assert "3 child orders refused in a row; last: trading is stopped" in view.reason


async def test_a_fill_that_crosses_the_stop_is_still_hedged(world):
    algo = spread(world)
    await world.engine.settle()
    (q,) = world.engine.get(algo.algo_id).children
    world.engine.stop(algo.algo_id)
    fill(world, q, 150, 2501000)  # the quote filled before the cancel landed
    await world.engine.settle()
    assert legs(world)[-1] == ("NIFTY26NOVFUT", "SELL", 150, 2513980)
    (_, hedge) = world.engine.get(algo.algo_id).children
    fill(world, hedge, 150, 2514000)
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.STOPPED and "UNHEDGED" not in view.reason


async def test_no_price_on_leg_b_means_no_quote(world):
    world.md.on_tick(Tick(NOV, 2514000, bids=(), asks=()))
    algo = spread(world)
    await world.engine.settle()
    assert world.broker.placed == []
    assert "no price on leg B" in world.engine.get(algo.algo_id).status


@pytest.mark.parametrize(
    ("leg2", "reason"),
    [
        ("NFO:NOPE", "not a known contract"),
        (OCT, "different contract"),
    ],
)
async def test_bad_leg_b_fails_with_the_reason(world, leg2, reason):
    algo = spread(world, leg2=leg2)
    await world.engine.settle()
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.FAILED and reason in view.reason


async def test_legs_with_different_lot_sizes_are_refused(world, nifty_fut):
    world.store.add(
        Instrument(
            Exchange.NFO, "BANKNIFTY26OCTFUT", InstrumentKind.FUTURE, tick_paise=10, lot_size=35
        )
    )
    algo = spread(world, leg2="NFO:BANKNIFTY26OCTFUT")
    await world.engine.settle()
    assert "different lot sizes" in world.engine.get(algo.algo_id).reason


async def test_a_nifty_calendar_spread_on_the_paper_broker(nifty_fut, nov):
    """Buy OCT − NOV at −130.00. The market first offers OCT too high; then OCT trades down
    to our quote, the quote fills, and NOV is sold at once."""
    store = InstrumentStore()
    store.add(nifty_fut)
    store.add(nov)
    md = MarketData()
    md.on_tick(quote(OCT, 2501020, 2501030))
    md.on_tick(quote(NOV, 2514000, 2514010))
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
            "SPREAD", user_id="ayush", instrument_id=OCT, side=Side.BUY, qty=150,
            params={"leg2": NOV, "level_paise": -13000, "payup_ticks": 2},
        )  # fmt: skip
        async with asyncio.timeout(5):
            while not runtime.orders.working():
                await asyncio.sleep(0.02)
        assert runtime.orders.working()[0].request.price_paise == 2501000  # resting at our level
        md.on_tick(quote(OCT, 2500990, 2501000))  # OCT offered at our price: the quote fills
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.02)
        view = runtime.algos.get(algo.algo_id)
        assert view.extra["leg_a_filled"] == view.extra["leg_b_filled"] == 150
        assert view.extra["traded_spread_paise"] == 2501000 - 2514000
        assert runtime.positions.net_qty(OCT) == 150 and runtime.positions.net_qty(NOV) == -150

    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()
