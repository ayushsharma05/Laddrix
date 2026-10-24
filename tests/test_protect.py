"""Protection held by MockTrade: trailing stop, OCO exit and bracket (#200)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoError, AlgoState
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.orders import OrderStatus, OrderType, OrderUpdate, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


def tick(ltp):
    return Tick(
        FUT, ltp, bids=(DepthLevel(ltp - 10, 150, 1),), asks=(DepthLevel(ltp + 10, 150, 1),)
    )


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def w(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(tick(2501000))
    broker = FakeBroker()
    clock = Clock()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    engine = AlgoEngine("acc-1", om, md, store.get, BUILTIN, clock=clock)

    class W:
        pass

    x = W()
    x.md, x.broker, x.om, x.engine, x.clock = md, broker, om, engine, clock
    return x


def start(w, code, side=Side.SELL, qty=150, **params):
    return w.engine.start(code, user_id="ayush", instrument_id=FUT, side=side, qty=qty,
                          params=params)  # fmt: skip


async def price(w, ltp):
    t = tick(ltp)
    w.md.on_tick(t)
    w.engine.on_tick(t)
    await w.engine.settle()


async def beat(w, seconds=0.1):
    w.clock.now += timedelta(seconds=seconds)
    w.engine.heartbeat()
    await w.engine.settle()


def kids(w, algo):
    return [w.om.get(c) for c in w.engine.get(algo.algo_id).children]


def fill(w, child, qty=None, price=None):
    qty = qty if qty is not None else child.leaves_qty
    price = price or child.request.price_paise or 2500000
    total = child.filled_qty + qty
    status = OrderStatus.FILLED if total >= child.request.qty else OrderStatus.PARTIAL
    w.om.apply(OrderUpdate(child.client_order_id, child.broker_order_id, status, total, price,
                           last_fill_qty=qty, last_fill_price_paise=price))  # fmt: skip


def cancelled(w, child):
    w.om.apply(OrderUpdate(child.client_order_id, child.broker_order_id, OrderStatus.CANCELLED,
                           child.filled_qty, child.avg_price_paise))  # fmt: skip


def view(w, algo):
    return w.engine.get(algo.algo_id)


# --- trailing stop ---


async def test_a_trailing_stop_follows_the_highs_never_back_then_exits(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000)  # protects a long: SELL exit
    await w.engine.settle()
    assert view(w, algo).extra["stop_paise"] == 2499000 and w.broker.placed == []
    await price(w, 2503000)
    assert view(w, algo).extra["stop_paise"] == 2501000  # moved up with the high
    await price(w, 2502000)
    assert view(w, algo).extra["stop_paise"] == 2501000  # never back down
    assert view(w, algo).extra["best_paise"] == 2503000
    assert w.broker.placed == []  # nothing at the exchange while it waits
    await price(w, 2501000)  # touched
    (exit_,) = w.broker.placed
    assert (exit_.side, exit_.qty, exit_.order_type) == (Side.SELL, 150, OrderType.MARKET)
    assert "touched at 25,010.00" in view(w, algo).status
    await price(w, 2490000)
    assert len(w.broker.placed) == 1  # one exit only
    fill(w, kids(w, algo)[0], price=2500900)
    await w.engine.settle()
    assert view(w, algo).state is AlgoState.DONE


async def test_a_percent_trail_protecting_a_short_follows_the_lows(w):
    await price(w, 2500000)
    algo = start(w, "TRAILING_STOP", side=Side.BUY, trail_pct=1.0)
    await w.engine.settle()
    assert view(w, algo).extra["stop_paise"] == 2525000
    await price(w, 2480000)
    assert view(w, algo).extra["stop_paise"] == 2504800  # 1% above the low, on the tick grid
    await price(w, 2504800)
    (exit_,) = w.broker.placed
    assert exit_.side is Side.BUY


async def test_a_percent_stop_sits_on_the_tick_grid_never_closer(w):
    algo = start(w, "TRAILING_STOP", trail_pct=0.33)  # 0.33% of 25,010.00 = 82.533
    await w.engine.settle()
    assert view(w, algo).extra["stop_paise"] == 2492740  # 24,927.467 rounded down to 0.10


async def test_a_limit_exit_has_slack_past_the_stop_and_a_gap_waits_at_it(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000, then="limit", limit_ticks=5)
    await w.engine.settle()
    await price(w, 2480000)  # a gap far through the 24,990.00 stop
    (exit_,) = w.broker.placed
    assert (exit_.order_type, exit_.price_paise) == (OrderType.LIMIT, 2499000 - 50)
    assert "with a limit at 24,989.50" in view(w, algo).status


async def test_the_trail_is_given_one_way(w):
    with pytest.raises(AlgoError, match="points or in percent"):
        start(w, "TRAILING_STOP")
    with pytest.raises(AlgoError, match="points or in percent"):
        start(w, "TRAILING_STOP", trail_paise=1000, trail_pct=1.0)


async def test_a_refused_exit_is_sent_again(w):
    algo = start(w, "TRAILING_STOP", trail_paise=2000)
    await w.engine.settle()
    w.broker.fail_next("exchange busy")
    await price(w, 2499000)
    assert [c.status for c in kids(w, algo)] == [OrderStatus.REJECTED]
    await beat(w)
    assert len(w.broker.placed) == 1 and kids(w, algo)[-1].status is not OrderStatus.REJECTED


# --- OCO exit ---


async def test_oco_stop_cancels_the_target_first_then_exits(w):
    algo = start(w, "OCO", target_paise=2510000, stop_paise=2495000)
    await w.engine.settle()
    (target,) = kids(w, algo)
    assert (target.request.price_paise, target.request.qty) == (2510000, 150)
    await price(w, 2495000)  # stop touched
    assert w.broker.cancelled == [target.broker_order_id]
    assert len(w.broker.placed) == 1  # no exit while the target might still fill
    cancelled(w, w.om.get(target.client_order_id))
    await w.engine.settle()
    exit_ = w.broker.placed[-1]
    assert (exit_.side, exit_.qty, exit_.order_type) == (Side.SELL, 150, OrderType.MARKET)


async def test_oco_after_a_part_filled_target_exits_only_the_rest(w):
    algo = start(w, "OCO", target_paise=2510000, stop_paise=2495000)
    await w.engine.settle()
    (target,) = kids(w, algo)
    fill(w, target, qty=75)
    await price(w, 2494000)
    cancelled(w, w.om.get(target.client_order_id))
    await w.engine.settle()
    assert w.broker.placed[-1].qty == 75  # never more than is still open


async def test_oco_target_filled_means_done_and_no_stop_later(w):
    algo = start(w, "OCO", target_paise=2510000, stop_paise=2495000)
    await w.engine.settle()
    fill(w, kids(w, algo)[0])
    await w.engine.settle()
    assert view(w, algo).state is AlgoState.DONE
    await price(w, 2480000)
    assert len(w.broker.placed) == 1


async def test_oco_refuses_levels_the_wrong_way_round_or_already_passed(w):
    bad = start(w, "OCO", target_paise=2490000, stop_paise=2495000)
    await w.engine.settle()
    assert view(w, bad).state is AlgoState.FAILED
    assert "target must be above the stop" in view(w, bad).reason
    passed = start(w, "OCO", target_paise=2520000, stop_paise=2505000)  # LTP 25,010 < stop
    await w.engine.settle()
    assert "already past the stop" in view(w, passed).reason
    assert w.broker.placed == []


# --- bracket ---


async def test_a_bracket_places_a_target_for_each_entry_fill(w):
    algo = start(w, "BRACKET", side=Side.BUY, entry_paise=2500000,
                 target_points_paise=3000, stop_points_paise=2000)  # fmt: skip
    await w.engine.settle()
    (entry,) = kids(w, algo)
    assert (entry.request.side, entry.request.price_paise) == (Side.BUY, 2500000)
    fill(w, entry, qty=75)
    await w.engine.settle()
    extra = view(w, algo).extra
    assert (extra["target_paise"], extra["stop_paise"]) == (2503000, 2498000)
    targets = [c for c in kids(w, algo) if c.request.side is Side.SELL]
    assert [(t.request.qty, t.request.price_paise) for t in targets] == [(75, 2503000)]
    fill(w, w.om.get(entry.client_order_id), qty=75)
    await w.engine.settle()
    targets = [c for c in kids(w, algo) if c.request.side is Side.SELL]
    assert [t.request.qty for t in targets] == [75, 75]  # exits never exceed what's in
    for t in targets:
        fill(w, t)
    await w.engine.settle()
    v = view(w, algo)
    assert v.state is AlgoState.DONE and v.filled_qty == 150  # entry side only


async def test_bracket_stop_pulls_entry_and_targets_then_exits_what_is_open(w):
    algo = start(w, "BRACKET", side=Side.BUY, entry_paise=2500000,
                 target_points_paise=3000, stop_points_paise=2000)  # fmt: skip
    await w.engine.settle()
    (entry,) = kids(w, algo)
    fill(w, entry, qty=75)
    await w.engine.settle()
    await price(w, 2498000)  # stop
    target = next(c for c in kids(w, algo) if c.request.side is Side.SELL)
    assert set(w.broker.cancelled) == {entry.broker_order_id, target.broker_order_id}
    assert not any(r.order_type is OrderType.MARKET for r in w.broker.placed)  # not yet
    cancelled(w, w.om.get(entry.client_order_id))
    cancelled(w, w.om.get(target.client_order_id))
    await w.engine.settle()
    exit_ = w.broker.placed[-1]
    assert (exit_.side, exit_.qty, exit_.order_type) == (Side.SELL, 75, OrderType.MARKET)
    fill(w, kids(w, algo)[-1], price=2497900)
    await w.engine.settle()
    assert view(w, algo).state is AlgoState.DONE and view(w, algo).extra["open_qty"] == 0


async def test_stopping_a_bracket_with_a_position_says_it_is_unprotected(w):
    algo = start(w, "BRACKET", side=Side.BUY, target_points_paise=3000, stop_points_paise=2000)
    await w.engine.settle()
    (entry,) = kids(w, algo)
    assert entry.request.order_type is OrderType.MARKET  # no entry price: at the market
    fill(w, entry, price=2501010)
    await w.engine.settle()
    w.engine.stop(algo.algo_id)
    await w.engine.settle()
    for c in kids(w, algo):
        if not c.status.is_final:
            cancelled(w, c)
    await beat(w)
    assert "UNPROTECTED: 150 of NFO:NIFTY26OCTFUT" in view(w, algo).reason


async def test_protection_algos_are_published_as_their_own_family():
    families = {t.spec.code: t.spec.family for t in BUILTIN}
    assert {families[c] for c in ("TRAILING_STOP", "OCO", "BRACKET")} == {"protection"}
    held = [t.spec.help for t in BUILTIN if t.spec.family == "protection"]
    assert all("Held by MockTrade" in h for h in held)


# --- a restart: what was protecting is named, once ---


async def test_protection_running_at_a_restart_is_reported_once(tmp_path, nifty_fut):
    def platform():
        store, md = InstrumentStore(), MarketData()
        store.add(nifty_fut)
        md.on_tick(tick(2501000))
        log = EventLog(tmp_path / "events.db")
        log.start()
        p = Platform(store, md, KillSwitch("firm"), log)
        rt = p.add_account(BrokerAccount("acc", "paper", "P1", "ayush"), FakeBroker(),
                           ids=ClientIds("MT"), algo_types=BUILTIN)  # fmt: skip
        return p, rt, log

    p, rt, log = platform()
    stop = rt.algos.start("TRAILING_STOP", user_id="ayush", instrument_id=FUT, side=Side.SELL,
                          qty=75, params={"trail_paise": 2000})  # fmt: skip
    slicer = rt.algos.start("SLICER", user_id="ayush", instrument_id=FUT, side=Side.BUY,
                            qty=75, params={"slices": 1, "interval_s": 60})  # fmt: skip
    await asyncio.sleep(0)
    await rt.algos.settle()
    assert slicer  # execution algos are not protection: not reported
    await log.aclose()

    p, rt, log = platform()  # the restart
    (lost,) = p.lost_protection("acc")
    assert lost.instrument_id == FUT
    assert lost.words.startswith(f"TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT ({stop.algo_id}, ayush)")
    await log.flush()
    assert p.lost_protection("acc") == []  # reported once
    await log.aclose()


async def test_a_bracket_is_not_done_while_its_entry_is_still_working(w):
    algo = start(w, "BRACKET", side=Side.BUY, entry_paise=2500000,
                 target_points_paise=3000, stop_points_paise=2000)  # fmt: skip
    await w.engine.settle()
    (entry,) = kids(w, algo)
    fill(w, entry, qty=75)
    await w.engine.settle()
    target = next(c for c in kids(w, algo) if c.request.side is Side.SELL)
    fill(w, target)  # the half that came in has gone out at the target
    await w.engine.settle()
    assert view(w, algo).state is AlgoState.WORKING
    assert not w.om.get(entry.client_order_id).status.is_final  # the rest of the entry waits
    assert entry.broker_order_id not in w.broker.cancelled


async def test_bracket_levels_from_an_average_off_the_tick_grid_land_on_it(w):
    algo = start(w, "BRACKET", side=Side.BUY, entry_paise=2500000,
                 target_points_paise=3000, stop_points_paise=2000)  # fmt: skip
    await w.engine.settle()
    fill(w, kids(w, algo)[0], qty=150, price=2500005)  # a broker's average of several trades
    await w.engine.settle()
    extra = view(w, algo).extra
    # toward the entry: a target a little easier to reach, a stop a little tighter
    assert (extra["target_paise"], extra["stop_paise"]) == (2503000, 2498010)
