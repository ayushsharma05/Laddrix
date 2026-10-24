"""Several quoting legs at once (#427): every quoting leg rests a quote; a fill on any of them
is hedged on every other leg, and all quotes together never carry more spread lots than are
still to do.

Prices as in test_autospreader.py (bid / ask): OCT 25,010.00 / 25,010.10, NOV 25,140.00 /
25,140.10. Buying the calendar OCT − NOV at −130.00, quoting both legs:
    OCT's quote: buy at 25,140.00 (NOV's bid) − 130.00 = 25,010.00
    NOV's quote: sell at 25,010.10 (OCT's ask) + 130.00 = 25,140.10
A fill on OCT is hedged by selling NOV at 25,139.80 (bid − 2 ticks); a fill on NOV by buying
OCT at 25,010.30 (ask + 2 ticks).
"""

from dataclasses import dataclass

import pytest
from test_autospreader import AYUSH, NOV, OCT, W

from mocktrade.algos.autospreader import AutoSpreader
from mocktrade.algos.engine import AlgoState
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from mocktrade.spreads.model import SpreadLeg


@pytest.fixture
def w(nifty_fut):
    return W(nifty_fut)


def both(w, payup=2):
    legs = [SpreadLeg(OCT, 1, 1, quote=True, payup_ticks=payup),
            SpreadLeg(NOV, -1, -1, quote=True, payup_ticks=payup)]  # fmt: skip
    return w.book.create(AYUSH, "Oct/Nov both", legs)


def by_role(w, algo):
    out = {}
    for c in w.children(algo):
        r = w.om.get(c).request
        out.setdefault(r.algo_role, []).append((r.instrument_id[4:13], r.side.value, r.qty,
                                                r.price_paise))  # fmt: skip
    return out


def quote_on(w, algo, leg):
    (q,) = [c for c in w.children(algo, leg) if w.om.get(c).request.algo_role == "quote"]
    return q


async def test_every_quoting_leg_quotes_its_share_at_the_spread_price(w):
    algo = w.start(both(w), -13000, lots=2)
    await w.engine.settle()
    assert by_role(w, algo) == {"quote": [("NIFTY26OC", "BUY", 75, 2501000),
                                          ("NIFTY26NO", "SELL", 75, 2514010)]}  # fmt: skip


async def test_a_fill_on_either_quote_is_hedged_on_the_other_leg_until_done(w):
    algo = w.start(both(w), -13000, lots=2)
    await w.engine.settle()
    w.fill(quote_on(w, algo, OCT), 75, 2501000)
    await w.engine.settle()
    assert by_role(w, algo)["hedge"] == [("NIFTY26NO", "SELL", 75, 2513980)]
    w.fill(quote_on(w, algo, NOV), 75, 2514010)
    await w.engine.settle()
    assert by_role(w, algo)["hedge"] == [("NIFTY26NO", "SELL", 75, 2513980),
                                         ("NIFTY26OC", "BUY", 75, 2501030)]  # fmt: skip
    for c in w.children(algo):
        r = w.om.get(c).request
        if r.algo_role == "hedge":
            w.fill(c, 75, r.price_paise)
    view = w.engine.get(algo.algo_id)
    assert view.state is AlgoState.DONE and view.extra["lots_done"] == 2
    assert view.extra["legs"] == "NIFTY26OCTFUT bought 150 · NIFTY26NOVFUT sold 150"
    assert len(w.broker.placed) == 4  # two quotes, two hedges: nothing more


async def test_both_quotes_filling_at_the_same_moment_never_overfill(w):
    algo = w.start(both(w), -13000, lots=2)
    await w.engine.settle()
    oct_q, nov_q = quote_on(w, algo, OCT), quote_on(w, algo, NOV)
    w.fill(oct_q, 75, 2501000)
    w.fill(nov_q, 75, 2514010)  # before anything was sent for the first
    await w.engine.settle()
    hedges = sorted(by_role(w, algo)["hedge"])
    assert hedges == [("NIFTY26NO", "SELL", 75, 2513980), ("NIFTY26OC", "BUY", 75, 2501030)]
    w.engine.on_tick(w.md.latest(NOV))
    w.engine.heartbeat()
    await w.engine.settle()
    assert len(by_role(w, algo)["quote"]) == 2  # 2 of 2 spread lots committed: no new quote


async def test_one_spread_lot_is_quoted_on_one_leg_only(w):
    algo = w.start(both(w), -13000, lots=1)
    await w.engine.settle()
    assert by_role(w, algo) == {"quote": [("NIFTY26OC", "BUY", 75, 2501000)]}


async def test_the_lots_left_are_shared_out_and_requoted_as_quotes_fill(w):
    algo = w.start(both(w), -13000, lots=3)
    await w.engine.settle()
    assert by_role(w, algo)["quote"] == [("NIFTY26OC", "BUY", 75, 2501000),
                                         ("NIFTY26NO", "SELL", 150, 2514010)]  # fmt: skip
    w.fill(quote_on(w, algo, OCT), 75, 2501000)  # OCT's share done
    await w.engine.settle()
    (hedge,) = [c for c in w.children(algo, NOV) if w.om.get(c).request.algo_role == "hedge"]
    w.fill(hedge, 75, 2513980)
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    # 1 done + 2 resting on NOV = 3: nothing is left for OCT to quote
    assert len(by_role(w, algo)["quote"]) == 2


async def test_a_repeated_fill_update_never_hedges_twice(w):
    algo = w.start(both(w), -13000, lots=2)
    await w.engine.settle()
    q = quote_on(w, algo, OCT)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    view = w.om.get(q)
    w.om.apply(OrderUpdate(q, view.broker_order_id, OrderStatus.FILLED, 75, 2501000,
                           last_fill_qty=75, last_fill_price_paise=2501000))  # fmt: skip
    w.engine.heartbeat()
    await w.engine.settle()
    assert len(by_role(w, algo)["hedge"]) == 1


async def test_stop_pulls_every_quote_and_keeps_the_hedges(w):
    algo = w.start(both(w), -13000, lots=2)
    await w.engine.settle()
    w.fill(quote_on(w, algo, OCT), 75, 2501000)
    await w.engine.settle()
    (hedge,) = [c for c in w.children(algo, NOV) if w.om.get(c).request.algo_role == "hedge"]
    w.engine.stop(algo.algo_id)
    await w.engine.settle()
    assert w.om.get(quote_on(w, algo, NOV)).broker_order_id in w.broker.cancelled
    assert w.om.get(hedge).broker_order_id not in w.broker.cancelled


@dataclass
class _Child:
    request: OrderRequest


def test_orders_kept_from_before_427_tell_a_quote_by_its_leg_and_side(w):
    spread = w.calendar()
    ase = AutoSpreader({"spread": spread.spread_id, "level_paise": -13000}, w.book.get)
    old = OrderRequest("MT1", "acc-1", "ayush", OCT, Side.BUY, 75, OrderType.LIMIT,
                       Product.NRML, price_paise=2501000)  # fmt: skip
    assert ase._is_quote(_Child(old))  # the parent leg's own side: its quote
    assert not ase._is_quote(_Child(OrderRequest(**{**old.__dict__, "instrument_id": NOV,
                                                    "side": Side.SELL})))  # a hedge  # fmt: skip
    tagged = OrderRequest(**{**old.__dict__, "algo_role": "hedge"})
    assert not ase._is_quote(_Child(tagged))  # a role says it, whatever the leg


async def test_both_legs_quoting_on_the_paper_broker(nifty_fut):
    """Buy 2 calendars quoting both legs: the market reaches the OCT quote, then the NOV one;
    each is hedged at once; the account ends long 150 OCT, short 150 NOV, at the price asked."""
    import asyncio

    from test_autospreader import quote

    from mocktrade.algos.autospreader import algo_type
    from mocktrade.algos.builtin import BUILTIN
    from mocktrade.broker.paper import PaperBroker
    from mocktrade.domain.accounts import BrokerAccount
    from mocktrade.orders.model import ClientIds
    from mocktrade.platform import Platform
    from mocktrade.risk.kill_switch import KillSwitch
    from mocktrade.risk.limits import RiskLimits

    w = W(nifty_fut)
    md, store = w.md, w.store
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"), paper, ids=ClientIds("MT"),
        limits=RiskLimits(max_order_value_paise=10**12),
        algo_types=(*BUILTIN, algo_type(w.book.get)),
    )  # fmt: skip
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        spread = both(w)
        algo = runtime.algos.start("ASE", user_id="ayush", instrument_id=OCT, side=Side.BUY,
                                   qty=150, params={"spread": spread.spread_id,
                                                    "level_paise": -13000})  # fmt: skip
        async with asyncio.timeout(5):
            while len(runtime.orders.working()) < 2:
                await asyncio.sleep(0.02)
        # OCT offered at our quote: it fills; NOV's quote then sits at OCT's new ask + 130.00
        # = 25,140.00, NOV's bid: it fills too; each is hedged on the other leg at once
        md.on_tick(quote(OCT, 2500990, 2501000))
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.02)
        assert runtime.algos.get(algo.algo_id).extra["traded_spread_paise"] == -13000
        assert [runtime.positions.net_qty(i) for i in (OCT, NOV)] == [150, -150]
        roles = sorted(o.request.algo_role for o in runtime.orders.orders())
        assert roles == ["hedge", "hedge", "quote", "quote"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()


def test_an_orders_role_is_kept_with_it_and_absent_in_older_records():
    from mocktrade.orders.codec import request_from_dict, request_to_dict

    r = OrderRequest("MT1", "acc-1", "ayush", OCT, Side.BUY, 75, OrderType.LIMIT, Product.NRML,
                     price_paise=2501000, parent_id="A1-1", algo_role="hedge")  # fmt: skip
    assert request_from_dict(request_to_dict(r)) == r
    old = {k: v for k, v in request_to_dict(r).items() if k != "algo_role"}
    assert request_from_dict(old).algo_role == ""


async def test_a_quote_on_its_way_counts_before_it_is_registered(w):
    algo = w.start(both(w), -13000, lots=1)  # OCT quotes the one lot…
    for _ in range(2):  # …and ticks come before the order manager has registered it
        w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert by_role(w, algo) == {"quote": [("NIFTY26OC", "BUY", 75, 2501000)]}  # nothing more


async def test_each_quoting_leg_rounds_its_own_way_so_the_spread_is_never_worse(w):
    algo = w.start(both(w), -12995, lots=2)
    await w.engine.settle()
    # buying OCT − NOV at −129.95: OCT ≤ 25,140.00 − 129.95 = 25,010.05 → 25,010.00 (down);
    # NOV ≥ 25,010.10 + 129.95 = 25,140.05 → 25,140.10 (up: selling NOV, a higher price helps)
    assert by_role(w, algo)["quote"] == [("NIFTY26OC", "BUY", 75, 2501000),
                                         ("NIFTY26NO", "SELL", 75, 2514010)]  # fmt: skip
