"""The Autospreader's controls and actions (#425): the re-quote threshold, max unhedged, the
hedge chase and timeout, "Hedge now" and "Flatten", and hedges kept while paused.

Prices as in test_autospreader.py (bid / ask), calendar OCT − NOV quoting OCT, pay-up 2:
    OCT 25,010.00 / 25,010.10     NOV 25,140.00 / 25,140.10
Buying the calendar at −130.00 quotes OCT at 25,140.00 − 130.00 = 25,010.00, and a fill is
hedged by selling NOV at its bid − 2 ticks = 25,139.80.
"""

from datetime import timedelta

import pytest
from test_autospreader import AYUSH, NOV, OCT, W, quote

from mocktrade.algos.engine import AlgoError, AlgoState
from mocktrade.broker.rules import protected_limit
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Side
from mocktrade.domain.ticks import Tick
from mocktrade.server.formats import algo_to_dict
from mocktrade.spreads.model import Controls, SpreadLeg, controls_from_dict, spread_from_dict


@pytest.fixture
def w(nifty_fut):
    return W(nifty_fut)


def calendar(w, **controls):
    legs = [SpreadLeg(OCT, 1, 1, quote=True, payup_ticks=2), SpreadLeg(NOV, -1, -1, payup_ticks=2)]
    return w.book.create(AYUSH, "Oct/Nov", legs, Controls(**controls))


def nov(bid, ask):
    """NOV moves; the engine hears it."""
    return quote(NOV, bid, ask)


async def move(w, tick):
    w.md.on_tick(tick)
    w.engine.on_tick(w.md.latest(tick.instrument_id))
    await w.engine.settle()


def cancelled(w, child):
    view = w.om.get(child)
    w.om.apply(OrderUpdate(child, view.broker_order_id, OrderStatus.CANCELLED, view.filled_qty,
                           view.avg_price_paise or 0))  # fmt: skip


# --- the controls in the definition ---------------------------------------------------------


def test_controls_are_checked_in_words_and_default_when_missing():
    assert controls_from_dict(None) == (Controls(), [])
    assert controls_from_dict({"chase_ticks": 3}) == (Controls(chase_ticks=3), [])
    _, problems = controls_from_dict({"requote_ticks": 0, "hedge_timeout_s": 900, "x": 1})
    assert problems == ["Autospreader controls has no setting called 'x'",
                        "Re-quote threshold must be at least 1",
                        "Hedge timeout must be at most 300"]  # fmt: skip
    old = {"spread_id": "s1", "user_id": "ayush", "workspace": "", "name": "n",
           "legs": [{"instrument_id": OCT, "ratio": 1, "multiplier": 1, "quote": True},
                    {"instrument_id": NOV, "ratio": -1, "multiplier": -1}],
           "created_at": "2026-10-24T10:00:00+00:00", "updated_at": "2026-10-24T10:00:00+00:00"}  # fmt: skip
    assert spread_from_dict(old).controls == Controls()  # saved before #425


# --- the re-quote threshold and max unhedged --------------------------------------------------


async def test_the_quote_waits_for_the_threshold_but_never_rests_worse(w):
    w.start(calendar(w, requote_ticks=3), -13000)
    await w.engine.settle()
    assert w.placed() == [("NIFTY26OC", "BUY", 150, 2501000)]
    await move(w, nov(2514010, 2514020))  # target 25,010.10: a better chance, 1 tick
    await move(w, nov(2514020, 2514030))  # 2 ticks
    assert w.broker.modified == []
    await move(w, nov(2514030, 2514040))  # 3 ticks: moved
    assert [c["price_paise"] for _, c in w.broker.modified] == [2501030]


async def test_a_move_that_would_make_the_spread_worse_is_made_at_once(w):
    w.start(calendar(w, requote_ticks=10), -13000)
    await w.engine.settle()
    await move(w, nov(2513990, 2514000))  # target 25,009.90: resting at 25,010.00 is worse
    assert [c["price_paise"] for _, c in w.broker.modified] == [2500990]


async def test_a_quote_never_carries_more_than_max_unhedged(w):
    algo = w.start(calendar(w, max_unhedged_lots=3), -13000, lots=10)
    await w.engine.settle()
    assert w.placed() == [("NIFTY26OC", "BUY", 225, 2501000)]  # 3 of 10 spread lots
    (q,) = w.children(algo)
    w.fill(q, 225, 2501000)
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 225, 2514000)
    await move(w, nov(2514000, 2514010))
    assert w.placed()[-1] == ("NIFTY26OC", "BUY", 225, 2501000)  # the next 3


# --- the chase and the timeout ----------------------------------------------------------------


async def test_a_hedge_chases_the_market_but_never_past_its_cap(w):
    algo = w.start(calendar(w, chase_ticks=3), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    assert w.om.get(hedge).request.price_paise == 2513980
    await move(w, nov(2513970, 2513980))  # bid down 3: best − 2 = 25,139.50; cap 25,139.50
    await move(w, nov(2513900, 2513910))  # far away: still the cap, so no second move
    assert [(b, c["price_paise"]) for b, c in w.broker.modified] == [
        (w.om.get(hedge).broker_order_id, 2513950)]  # fmt: skip


async def test_no_chase_when_it_is_off_and_a_hedge_never_moves_back(w):
    algo = w.start(calendar(w), -13000, lots=1)  # chase 0
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    await move(w, nov(2513900, 2513910))
    assert w.broker.modified == []


async def test_a_hedge_never_chases_back_towards_its_first_price(w):
    algo = w.start(calendar(w, chase_ticks=5), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    await move(w, nov(2514100, 2514110))  # the market comes towards the hedge: it stays
    assert w.broker.modified == []


async def test_a_leg_unhedged_too_long_alerts_once_and_keeps_saying_it(w):
    events = []
    w.engine.subscribe(lambda e: events.append(e))
    algo = w.start(calendar(w, hedge_timeout_s=5), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    w.clock.now += timedelta(seconds=4)
    w.engine.heartbeat()
    assert [e for e in events if e.kind == "ALERT"] == []
    w.clock.now += timedelta(seconds=2)
    w.engine.heartbeat()
    w.clock.now += timedelta(seconds=1)
    w.engine.heartbeat()
    alerts = [e.detail for e in events if e.kind == "ALERT"]
    assert alerts == ["Oct/Nov: UNHEDGED 75 on NIFTY26NOVFUT for 6 s: hedge now or flatten"]
    view = w.engine.get(algo.algo_id)
    assert view.status == "UNHEDGED 75 on NIFTY26NOVFUT for 7 s: hedge now or flatten"
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 75, 2514000)
    assert w.engine.get(algo.algo_id).state is AlgoState.DONE


# --- the actions ----------------------------------------------------------------------------


async def test_hedge_now_moves_working_hedges_and_sends_what_nothing_covers(w):
    algo = w.start(calendar(w), -13000, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)  # 1 lot: hedge 75 working
    await w.engine.settle()
    (h1,) = w.children(algo, NOV)
    cross = protected_limit(w.store.get(NOV), Side.SELL, 2514000, -1)
    preview = w.engine.preview(algo.algo_id, "hedge_now")
    assert preview.ok and preview.lines == ("move SELL 75 NIFTY26NOVFUT to cross the market",)
    view = w.engine.act(algo.algo_id, "hedge_now", list(preview.lines))
    await w.engine.settle()
    assert w.broker.modified == [(w.om.get(h1).broker_order_id,
                                  {"qty": None, "price_paise": cross, "trigger_paise": None})]  # fmt: skip
    assert [a["name"] for a in algo_to_dict(view)["actions"]] == ["hedge_now", "flatten"]



async def test_hedge_now_sends_what_nothing_covers(nifty_fut):
    blocked = {"on": False}  # NOV's per-order limit: not one lot while "on" (e.g. limits cut)
    w = W(nifty_fut, lambda i, _p: 0 if i == NOV and blocked["on"] else 750)
    algo = w.start(calendar(w), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    blocked["on"] = True
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    assert w.children(algo, NOV) == []  # the hedge couldn't go
    blocked["on"] = False
    cross = protected_limit(w.store.get(NOV), Side.SELL, 2514000, -1)
    preview = w.engine.preview(algo.algo_id, "hedge_now")
    assert preview.lines == ("SELL 75 NIFTY26NOVFUT, crossing the market",)
    with pytest.raises(AlgoError, match="what it would do has changed since you looked"):
        w.engine.act(algo.algo_id, "hedge_now", ["move SELL 75 NIFTY26NOVFUT to cross the market"])
    w.engine.act(algo.algo_id, "hedge_now", list(preview.lines))
    w.engine.act(algo.algo_id, "hedge_now", [])  # a second click: covered now, nothing sent
    await w.engine.settle()
    assert w.placed()[1:] == [("NIFTY26NO", "SELL", 75, cross)]


async def test_hedge_now_says_why_it_cannot(w):
    algo = w.start(calendar(w), -13000, lots=1)
    await w.engine.settle()
    nothing = w.engine.preview(algo.algo_id, "hedge_now")
    assert (nothing.ok, nothing.why) == (False, "nothing is unhedged")
    with pytest.raises(AlgoError, match="nothing is unhedged"):
        w.engine.act(algo.algo_id, "hedge_now", [])
    with pytest.raises(AlgoError, match="ASE has no action 'dance'"):
        w.engine.preview(algo.algo_id, "dance")
    (q,) = w.children(algo)
    w.md.on_tick(Tick(NOV, 2514000))  # NOV's depth is gone
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    gone = w.engine.preview(algo.algo_id, "hedge_now")
    assert (gone.ok, gone.why) == (False, "NIFTY26NOVFUT has no price")


async def test_flatten_cancels_then_undoes_only_the_unhedged_part(w):
    algo = w.start(calendar(w), -13000, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 150, 2501000)  # 2 lots of OCT
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 75, 2514000)  # 1 of 2 hedged: 1 spread complete, 75 OCT unhedged
    preview = w.engine.preview(algo.algo_id, "flatten")
    assert preview.lines == (
        "cancel SELL 75 NIFTY26NOVFUT",
        "SELL 75 NIFTY26OCTFUT, crossing the market (if still unhedged once the cancels are "
        "done)",
    )
    w.engine.act(algo.algo_id, "flatten", list(preview.lines))
    await w.engine.settle()
    assert w.broker.cancelled == [w.om.get(hedge).broker_order_id]
    assert len(w.broker.placed) == 2  # nothing closed before the cancel lands
    cancelled(w, hedge)
    await w.engine.settle()
    cross = protected_limit(w.store.get(OCT), Side.SELL, 2501000, -1)
    assert w.placed()[-1] == ("NIFTY26OC", "SELL", 75, cross)
    (close,) = [c for c in w.children(algo, OCT) if c != q]
    w.fill(close, 75, 2501000)
    view = w.engine.get(algo.algo_id)
    assert view.state is AlgoState.STOPPED
    assert view.reason == "flattened: NIFTY26OCTFUT 75 · NIFTY26NOVFUT 75"  # one whole spread
    assert "UNHEDGED" not in view.reason and view.extra["unhedged"] == 0


async def test_flatten_with_nothing_unhedged_says_use_stop(w):
    algo = w.start(calendar(w), -13000, lots=1)
    await w.engine.settle()
    view = w.engine.preview(algo.algo_id, "flatten")
    assert (view.ok, view.why) == (False, "nothing is unhedged: use Stop")


async def test_a_fill_the_cancel_crossed_is_counted_before_anything_is_closed(w):
    algo = w.start(calendar(w), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    preview = w.engine.preview(algo.algo_id, "flatten")
    w.engine.act(algo.algo_id, "flatten", list(preview.lines))
    await w.engine.settle()
    w.fill(hedge, 75, 2514000)  # the hedge filled before the cancel landed: a whole spread
    await w.engine.settle()
    assert len(w.broker.placed) == 2  # nothing to close
    view = w.engine.get(algo.algo_id)
    assert view.state is AlgoState.STOPPED and view.reason.startswith("flattened")


# --- hedges while paused (the engine) ---------------------------------------------------------


async def test_a_hedge_sent_while_paused_stays_working(w):
    algo = w.start(calendar(w), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.engine.pause(algo.algo_id)
    w.fill(q, 75, 2501000)  # the fill crossed the pause
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    assert w.om.get(hedge).broker_order_id not in w.broker.cancelled
    w.fill(hedge, 75, 2514000)
    assert w.engine.get(algo.algo_id).extra["unhedged"] == 0


async def test_the_timeout_and_a_retried_hedge_still_run_while_paused(w):
    events = []
    w.engine.subscribe(lambda e: events.append(e))
    algo = w.start(calendar(w, hedge_timeout_s=1), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.engine.pause(algo.algo_id)
    w.md.on_tick(Tick(NOV, 2514000))  # no NOV price: the hedge waits
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    assert w.children(algo, NOV) == []
    w.clock.now += timedelta(seconds=2)
    w.engine.heartbeat()
    assert [e.kind for e in events].count("ALERT") == 1
    w.md.on_tick(nov(2514000, 2514010))  # a price again: the paused algo hedges
    w.engine.heartbeat()
    await w.engine.settle()
    assert len(w.children(algo, NOV)) == 1
