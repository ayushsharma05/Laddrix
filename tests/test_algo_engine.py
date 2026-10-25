"""The algo engine: parent orders, children through the order manager, stop and safety rules."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta

import pytest

from laddrix.algos.engine import (
    MAX_REJECTS_IN_A_ROW,
    AlgoContext,
    AlgoEngine,
    AlgoError,
    AlgoState,
    AlgoType,
    BaseStrategy,
)
from laddrix.algos.spec import AlgoSpec, ParamKind, ParamSpec
from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import OrderStatus, OrderUpdate, Side
from laddrix.domain.ticks import Tick
from laddrix.marketdata.service import MarketData
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, Pending
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.throttle import OrderThrottle, RateRule

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


class Scripted(BaseStrategy):
    """Sends `slices` children of `lot` lots at start; records what it heard."""

    def __init__(self, params):
        self.params = params
        self.ticks, self.children, self.timers = [], [], []
        self.paused = self.resumed = 0

    def start(self, ctx):
        lot = ctx.instrument().lot_size
        for _ in range(self.params["slices"]):
            ctx.send(self.params["lots"] * lot, 2501000)
        ctx.say("working the slices")
        if self.params.get("explode"):
            raise RuntimeError("bug in the strategy")

    def on_tick(self, ctx, tick):
        self.ticks.append(tick.ltp_paise)

    def on_child(self, ctx, child, event):
        self.children.append((child.client_order_id, event.kind.value))

    def on_timer(self, ctx, now):
        self.timers.append(now)

    def on_pause(self, ctx):
        self.paused += 1

    def on_resume(self, ctx):
        self.resumed += 1


SPEC = AlgoSpec(
    "TEST",
    "Test",
    "sends slices",
    (
        ParamSpec("slices", "Slices", ParamKind.COUNT, "how many", min=1, max=10),
        ParamSpec("lots", "Lots per slice", ParamKind.LOTS, "size", default=1, min=1),
        ParamSpec("explode", "Break", ParamKind.FLAG, "raise at start", required=False),
    ),
)


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(Tick(FUT, 2501000))
    broker = FakeBroker()
    ticks = itertools.count()
    clock = lambda: T0 + timedelta(seconds=next(ticks))  # noqa: E731
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    audit = []
    blocked = {"why": None}
    engine = AlgoEngine(
        "acc-1",
        om,
        md,
        store.get,
        [AlgoType(SPEC, Scripted)],
        blocked=lambda: blocked["why"],
        clock=clock,
        audit=audit.append,
    )
    events = []
    engine.subscribe(events.append)

    class W:
        pass

    w = W()
    w.store, w.md, w.broker, w.om, w.engine, w.audit, w.events, w.blocked = (
        store, md, broker, om, engine, audit, events, blocked
    )  # fmt: skip
    return w


def start(w, slices=2, lots=1, qty=150, **extra):
    return w.engine.start(
        "TEST",
        user_id="ayush",
        instrument_id=FUT,
        side=Side.BUY,
        qty=qty,
        params={"slices": slices, "lots": lots, **extra},
    )


def strategy(w, algo_id):
    return w.engine._parents[algo_id].strategy


def fill(w, child_id, qty, price=2501000, total=None):
    view = w.om.get(child_id)
    filled = total if total is not None else qty
    status = OrderStatus.FILLED if filled >= view.request.qty else OrderStatus.PARTIAL
    w.om.apply(
        OrderUpdate(
            child_id, view.broker_order_id, status, filled, price,
            last_fill_qty=qty, last_fill_price_paise=price,
        )
    )  # fmt: skip


async def test_children_go_through_the_order_manager_marked_with_their_parent(world):
    algo = start(world, slices=2)
    await world.engine.settle()
    assert [r.parent_id for r in world.broker.placed] == [algo.algo_id] * 2
    assert all(r.algo_id is None for r in world.broker.placed)  # no exchange id we don't have
    view = world.engine.get(algo.algo_id)
    assert (view.state, len(view.children), view.working_qty) == (AlgoState.WORKING, 2, 150)
    assert view.status == "working the slices"
    assert [e.kind for e in world.audit] == ["STARTED"]


async def test_parent_fill_is_the_sum_of_its_children_and_never_double_counts(world):
    algo = start(world, slices=2)
    await world.engine.settle()
    a, b = world.engine.get(algo.algo_id).children
    fill(world, a, 25, total=25)
    fill(world, a, 25, total=25)  # the same update twice
    fill(world, b, 75, price=2501020)
    view = world.engine.get(algo.algo_id)
    assert view.filled_qty == 100
    assert view.avg_price_paise == round((25 * 2501000 + 75 * 2501020) / 100)
    fill(world, a, 50, total=75)
    await world.engine.settle()
    view = world.engine.get(algo.algo_id)
    assert (view.state, view.filled_qty, view.reason) == (AlgoState.DONE, 150, "fully filled")
    assert world.audit[-1].kind == "DONE"


async def test_a_parent_never_sends_more_than_its_quantity(world):
    algo = start(world, slices=5, lots=1, qty=150)  # asks for 5 x 75 of a 150 parent
    await world.engine.settle()
    assert sum(r.qty for r in world.broker.placed) == 150
    assert len(world.engine.get(algo.algo_id).children) == 2


async def test_stop_pulls_every_working_child_then_reports_stopped(world):
    algo = start(world, slices=2)
    await world.engine.settle()
    a, b = world.engine.get(algo.algo_id).children
    fill(world, a, 75)
    world.engine.stop(algo.algo_id, "changed my mind")
    await world.engine.settle()
    assert world.broker.cancelled == [world.om.get(b).broker_order_id]
    assert world.engine.get(algo.algo_id).state is AlgoState.STOPPING  # until the broker answers
    world.om.apply(OrderUpdate(b, world.om.get(b).broker_order_id, OrderStatus.CANCELLED))
    view = world.engine.get(algo.algo_id)
    assert (view.state, view.reason, view.filled_qty) == (AlgoState.STOPPED, "changed my mind", 75)
    with pytest.raises(AlgoError, match="already finished"):
        world.engine.pause(algo.algo_id)


async def test_pause_pulls_children_and_sends_nothing_until_resumed(world):
    algo = start(world, slices=1)
    await world.engine.settle()
    (a,) = world.engine.get(algo.algo_id).children
    world.engine.pause(algo.algo_id)
    await world.engine.settle()
    assert world.broker.cancelled == [world.om.get(a).broker_order_id]
    ctx_strategy = strategy(world, algo.algo_id)
    assert ctx_strategy.paused == 1
    world.engine.on_tick(Tick(FUT, 2501010))
    world.engine.heartbeat()
    assert ctx_strategy.ticks == [] and ctx_strategy.timers == []  # paused: no calls
    world.engine.resume(algo.algo_id)
    assert ctx_strategy.resumed == 1
    world.engine.on_tick(Tick(FUT, 2501020))
    world.engine.heartbeat()
    assert ctx_strategy.ticks == [2501020] and len(ctx_strategy.timers) == 1


async def test_a_kill_switch_stops_every_algo(world):
    one, two = start(world, slices=1), start(world, slices=1)
    await world.engine.settle()
    assert world.engine.stop_all("trading stopped: break") == 2
    await world.engine.settle()
    assert len(world.broker.cancelled) == 2
    world.blocked["why"] = "trading is stopped on acc-1"
    with pytest.raises(AlgoError, match="trading is stopped"):
        start(world)
    for algo in (one, two):
        assert world.engine.get(algo.algo_id).reason == "trading stopped: break"


async def test_an_unknown_child_outcome_holds_the_algo(world):
    algo = start(world, slices=1, qty=300)
    await world.engine.settle()
    (a,) = world.engine.get(algo.algo_id).children
    world.om._orders[a].pending = Pending.UNCONFIRMED  # the broker didn't answer
    ctx = AlgoContext(world.engine, world.engine._parents[algo.algo_id])
    assert ctx.send(75, 2501000) is False
    await world.engine.settle()
    assert len(world.broker.placed) == 1


async def test_strategy_code_that_breaks_fails_only_its_own_algo(world):
    good = start(world, slices=1)
    bad = start(world, slices=1, explode=True)  # sends a child, then raises
    await world.engine.settle()
    view = world.engine.get(bad.algo_id)
    assert "bug in the strategy" in view.reason
    # the child it sent just before breaking is pulled, not left working on its own
    (child,) = view.children
    child_view = world.om.get(child)
    assert child_view.pending is Pending.CANCEL or child_view.status.is_final
    if not child_view.status.is_final:
        world.om.apply(OrderUpdate(child, child_view.broker_order_id, OrderStatus.CANCELLED))
    assert world.engine.get(bad.algo_id).state is AlgoState.FAILED
    assert world.engine.get(good.algo_id).state is AlgoState.WORKING


async def test_a_child_sent_just_before_stop_is_pulled_too(world):
    algo = start(world, slices=1)
    world.engine.stop(algo.algo_id)  # before the child has reached the order manager
    assert world.engine.get(algo.algo_id).state is AlgoState.STOPPING  # one still on its way
    await world.engine.settle()
    (child,) = world.engine.get(algo.algo_id).children
    view = world.om.get(child)
    assert view.status.is_final or view.pending is Pending.CANCEL
    if not view.status.is_final:
        world.om.apply(OrderUpdate(child, view.broker_order_id, OrderStatus.CANCELLED))
    assert world.engine.get(algo.algo_id).state is AlgoState.STOPPED


async def test_repeated_refusals_fail_the_algo_with_the_reason(world):
    world.broker.fail_next("insufficient margin")
    algo = start(world, slices=1, qty=300)  # its first child is refused
    await world.engine.settle()
    ctx = AlgoContext(world.engine, world.engine._parents[algo.algo_id])
    for _ in range(MAX_REJECTS_IN_A_ROW - 1):
        assert world.engine.get(algo.algo_id).state is AlgoState.WORKING
        world.broker.fail_next("insufficient margin")
        ctx.send(75, 2501000)
        await world.engine.settle()
    view = world.engine.get(algo.algo_id)
    assert view.state is AlgoState.FAILED
    assert (
        view.reason
        == f"{MAX_REJECTS_IN_A_ROW} child orders refused in a row; last: insufficient margin"
    )


async def test_bad_settings_are_refused_with_every_problem_named(world):
    with pytest.raises(AlgoError) as err:
        world.engine.start(
            "TEST", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=100,
            params={"slices": 0, "colour": "red"},
        )  # fmt: skip
    reason = err.value.reason
    assert "Slices must be at least 1" in reason
    assert "no setting called 'colour'" in reason
    assert "multiple of the lot" in reason or "lot" in reason  # 100 isn't whole lots of 75
    with pytest.raises(AlgoError, match="no algo called"):
        world.engine.start(
            "NOPE", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=75, params={}
        )


async def test_leftover_children_from_before_a_restart_are_cancelled(world, nifty_fut):
    algo = start(world, slices=1)
    await world.engine.settle()
    # a restart: a new engine over the same order manager, with no memory of A1
    fresh = AlgoEngine("acc-1", world.om, world.md, world.store.get, [AlgoType(SPEC, Scripted)])
    orphans = await fresh.cancel_orphans()
    assert orphans == list(world.engine.get(algo.algo_id).children)
    assert len(world.broker.cancelled) == 1


async def test_children_wait_in_the_throttle_instead_of_being_refused(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    broker = FakeBroker()
    throttle = OrderThrottle((RateRule("per second", 1.0, limit=2, cancel_limit=3),))
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=throttle)
    engine = AlgoEngine("acc-1", om, md, store.get, [AlgoType(SPEC, Scripted)])
    engine.start(
        "TEST", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=300,
        params={"slices": 4, "lots": 1},
    )  # fmt: skip
    await asyncio.wait_for(engine.settle(), 5)
    assert len(broker.placed) == 4  # all sent, two of them after waiting their turn
