"""The Autospreader algo (#415): quote one leg of a multi-leg spread, hedge every other at once.

Prices (bid / ask) in every test unless it says otherwise:
    OCT 25,010.00 / 25,010.10     NOV 25,140.00 / 25,140.10     DEC 25,270.00 / 25,270.10

    calendar   OCT − NOV         quote OCT
    butterfly  OCT − 2 NOV + DEC  quote OCT (or NOV, ratio −2: hedges go in whole lots)
"""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from mocktrade.algos.autospreader import MODIFY_BUDGET, algo_type
from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoEngine, AlgoError, AlgoState
from mocktrade.broker.base import BrokerError
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount, User
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.spreads.model import SpreadLeg
from mocktrade.spreads.store import SpreadBook

OCT, NOV, DEC = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT", "NFO:NIFTY26DECFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
AYUSH = User("ayush", "Ayush")


def quote(instrument_id, bid, ask, qty=750):
    return Tick(instrument_id, bid, volume=10_000, bids=(DepthLevel(bid, qty, 3),),
                asks=(DepthLevel(ask, qty, 3),))  # fmt: skip


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class W:
    """One account's engine with the ASE algo, a fake broker, and a spread book."""

    def __init__(self, nifty_fut, order_cap=None):
        self.store = InstrumentStore()
        self.store.add(nifty_fut)
        self.store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
        self.store.add(replace(nifty_fut, symbol="NIFTY26DECFUT", expiry=date(2026, 12, 29)))
        self.md = MarketData()
        for t in (quote(OCT, 2501000, 2501010), quote(NOV, 2514000, 2514010),
                  quote(DEC, 2527000, 2527010)):  # fmt: skip
            self.md.on_tick(t)
        self.broker = FakeBroker()
        self.clock = Clock()
        self.book = SpreadBook(None, self.store.get, clock=self.clock)
        self.om = OrderManager("acc-1", self.broker, self.store.get, ids=ClientIds("MT"),
                               clock=self.clock)  # fmt: skip
        types = (*BUILTIN, algo_type(self.book.get))
        caps = {"order_cap": order_cap} if order_cap else {}
        self.engine = AlgoEngine("acc-1", self.om, self.md, self.store.get, types, clock=self.clock,
                                 **caps)  # fmt: skip

    def calendar(self, payup=2):
        legs = [SpreadLeg(OCT, 1, 1, quote=True, payup_ticks=payup),
                SpreadLeg(NOV, -1, -1, payup_ticks=payup)]  # fmt: skip
        return self.book.create(AYUSH, "Oct/Nov", legs)

    def butterfly(self, quote_on=OCT):
        legs = [SpreadLeg(OCT, 1, 1, quote=quote_on == OCT),
                SpreadLeg(NOV, -2, -2, quote=quote_on == NOV),
                SpreadLeg(DEC, 1, 1, quote=quote_on == DEC, payup_ticks=1)]  # fmt: skip
        return self.book.create(AYUSH, "fly", legs)

    def start(self, spread, level, *, buy=True, lots=2, user="ayush"):
        """Buy (or sell) `lots` spread lots at `level`: the parent is the quoting leg."""
        q = spread.quote_leg
        side = Side.BUY if (q.ratio > 0) == buy else Side.SELL
        params = {"spread": spread.spread_id, "level_paise": level}
        return self.engine.start("ASE", user_id=user, instrument_id=q.instrument_id, side=side,
                                 qty=lots * abs(q.ratio) * 75, params=params)  # fmt: skip

    def fill(self, child_id, qty, price):
        view = self.om.get(child_id)
        total = view.filled_qty + qty
        status = OrderStatus.FILLED if total >= view.request.qty else OrderStatus.PARTIAL
        self.om.apply(OrderUpdate(child_id, view.broker_order_id, status, total, price,
                                  last_fill_qty=qty, last_fill_price_paise=price))  # fmt: skip

    def placed(self):
        return [(r.instrument_id[4:13], r.side.value, r.qty, r.price_paise)
                for r in self.broker.placed]  # fmt: skip

    def children(self, algo, leg=None):
        ids = self.engine.get(algo.algo_id).children
        return [c for c in ids if leg is None or self.om.get(c).request.instrument_id == leg]


@pytest.fixture
def w(nifty_fut):
    return W(nifty_fut)


# --- the quote ------------------------------------------------------------------------------


async def test_a_calendar_quotes_its_first_leg_where_the_spread_equals_the_level(w):
    cal = w.calendar()
    w.start(cal, -13000)  # buy OCT − NOV at −130.00: OCT at NOV's bid 25,140.00 − 130.00
    await w.engine.settle()
    w.start(cal, -12990, buy=False)  # sell: OCT at NOV's ask 25,140.10 − 129.90
    await w.engine.settle()
    assert w.placed() == [("NIFTY26OC", "BUY", 150, 2501000), ("NIFTY26OC", "SELL", 150, 2501020)]


async def test_a_butterfly_quote_counts_every_other_leg_at_the_price_it_would_hedge_at(w):
    fly = w.butterfly()
    # buy OCT − 2·NOV + DEC at 0.00: sell 2 NOV at its bid, buy DEC at its ask:
    # OCT = 0 + 2 × 25,140.00 − 25,270.10 = 25,009.90
    w.start(fly, 0)
    await w.engine.settle()
    assert w.placed() == [("NIFTY26OC", "BUY", 150, 2500990)]


async def test_a_quote_on_a_minus_two_leg_rounds_so_the_spread_is_never_worse(w):
    fly = w.butterfly(quote_on=NOV)
    # buy the fly at 0.10 quoting NOV (sold, ×2): NOV = (25,010.10 + 25,270.10 − 0.10) / 2
    # = 25,140.05: selling NOV higher only helps a buyer, so it rounds up to 25,140.10
    w.start(fly, 10, lots=1)
    await w.engine.settle()
    assert w.placed() == [("NIFTY26NO", "SELL", 150, 2514010)]


async def test_it_requotes_when_another_leg_moves_within_the_modify_budget(w):
    algo = w.start(w.calendar(), -13000)
    await w.engine.settle()
    w.md.on_tick(quote(OCT, 2501010, 2501020))  # the quoting leg itself moving changes nothing
    w.engine.on_tick(w.md.latest(OCT))
    await w.engine.settle()
    assert w.broker.modified == []
    for n in range(MODIFY_BUDGET + 1):
        w.md.on_tick(quote(NOV, 2514010 + 10 * (n % 2), 2514020 + 10 * (n % 2)))
        w.engine.on_tick(w.md.latest(NOV))
        await w.engine.settle()
    assert len(w.broker.modified) == MODIFY_BUDGET  # then the quote is cancelled and re-sent
    assert w.broker.cancelled == [w.om.get(w.children(algo)[0]).broker_order_id]


async def test_no_quote_while_a_leg_has_no_price_or_the_legs_are_seconds_apart(w):
    w.md.on_tick(Tick(DEC, 2527000))  # no depth on DEC
    algo = w.start(w.butterfly(), 0)
    await w.engine.settle()
    assert w.broker.placed == [] and "a leg has no price" in w.engine.get(algo.algo_id).status
    w.md.on_tick(quote(DEC, 2527000, 2527010))
    times = iter([0.0, 10.0, 0.0])
    w.md._clock = lambda: next(times, 10.0)  # NOV's price is 10 s older than the others
    for t in (quote(OCT, 2501000, 2501010), quote(NOV, 2514000, 2514010),
              quote(DEC, 2527000, 2527010)):  # fmt: skip
        w.md.on_tick(t)
    w.engine.on_tick(w.md.latest(DEC))
    await w.engine.settle()
    assert w.broker.placed == [] and "s apart" in w.engine.get(algo.algo_id).status


async def test_the_quote_is_pulled_when_a_leg_loses_its_price(w):
    algo = w.start(w.calendar(), -13000)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.md.on_tick(Tick(NOV, 2514000))  # NOV's depth is gone: nothing to hedge against
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.broker.cancelled == [w.om.get(q).broker_order_id]


# --- hedging --------------------------------------------------------------------------------


async def test_every_other_leg_is_hedged_at_once_within_its_payup(w):
    algo = w.start(w.butterfly(), 0, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2500990)
    await w.engine.settle()
    # sell 2 lots of NOV at its bid − 2 ticks; buy 1 lot of DEC at its ask + 1 tick
    assert w.placed()[1:] == [("NIFTY26NO", "SELL", 150, 2513980),
                              ("NIFTY26DE", "BUY", 75, 2527020)]  # fmt: skip
    view = w.engine.get(algo.algo_id)
    assert view.extra["unhedged"] == 225 and view.state is AlgoState.WORKING
    assert view.extra["short"] == "150 on NIFTY26NOVFUT, 75 on NIFTY26DECFUT"
    (nov,) = w.children(algo, NOV)
    (dec,) = w.children(algo, DEC)
    w.clock.now += timedelta(milliseconds=240)
    w.fill(nov, 150, 2514000)
    assert w.engine.get(algo.algo_id).state is AlgoState.WORKING  # DEC still short
    w.fill(dec, 75, 2527010)
    view = w.engine.get(algo.algo_id)
    assert view.state is AlgoState.DONE
    assert view.extra["unhedged"] == 0 and view.extra["short"] == ""
    assert view.extra["traded_spread_paise"] == 2500990 - 2 * 2514000 + 2527010
    assert view.extra["max_legging_ms"] == 240
    assert view.extra["legs"] == ("NIFTY26OCTFUT bought 75 · NIFTY26NOVFUT sold 150 · "
                                  "NIFTY26DECFUT bought 75")  # fmt: skip
    assert view.extra["spread_name"] == "fly" and view.extra["spread_side"] == "BUY"


async def test_a_minus_two_quote_leg_hedges_in_whole_lots_only(w):
    algo = w.start(w.butterfly(quote_on=NOV), 10, lots=2)  # sell 4 lots of NOV
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2514010)  # 1 lot of NOV: half a butterfly, no whole lot to hedge yet
    await w.engine.settle()
    assert len(w.broker.placed) == 1
    w.fill(q, 75, 2514010)  # 2 lots: one butterfly: 1 OCT, 1 DEC
    await w.engine.settle()
    assert w.placed()[1:] == [("NIFTY26OC", "BUY", 75, 2501030), ("NIFTY26DE", "BUY", 75, 2527020)]


async def test_no_new_quote_goes_out_while_a_leg_is_unhedged(w):
    algo = w.start(w.calendar(), -13000, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)  # 1 of 2 spread lots: NOV is hedged next
    await w.engine.settle()
    view = w.om.get(q)
    w.om.apply(OrderUpdate(q, view.broker_order_id, OrderStatus.CANCELLED, 75, 2501000))
    w.engine.on_tick(w.md.latest(NOV))  # the quote is gone (cancelled outside the algo)
    await w.engine.settle()
    assert [r.instrument_id for r in w.broker.placed] == [OCT, NOV]  # no new OCT quote yet
    assert w.engine.get(algo.algo_id).extra["traded_spread_paise"] is None  # one leg so far
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 75, 2514000)  # hedged: now the rest is quoted
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.placed()[-1] == ("NIFTY26OC", "BUY", 75, 2501000)


async def test_partial_and_repeated_fills_never_over_hedge(w):
    algo = w.start(w.calendar(), -13000, lots=4)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    view = w.om.get(q)
    again = OrderUpdate(q, view.broker_order_id, view.status, view.filled_qty, 2501000,
                        last_fill_qty=75, last_fill_price_paise=2501000)  # fmt: skip
    w.om.apply(again)  # the same update a second time
    w.engine.heartbeat()
    await w.engine.settle()
    w.fill(q, 150, 2501000)
    await w.engine.settle()
    hedges = [r.qty for r in w.broker.placed if r.instrument_id == NOV]
    assert hedges == [75, 150]  # exactly what OCT filled: 225


async def test_stop_pulls_the_quote_keeps_hedges_and_names_each_leg_left_short(w):
    algo = w.start(w.butterfly(), 0, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 75, 2500990)
    await w.engine.settle()
    q_broker = w.om.get(q).broker_order_id
    w.engine.stop(algo.algo_id, "done for the day")
    await w.engine.settle()
    assert w.broker.cancelled == [q_broker]  # the quote, not the hedges
    w.om.apply(OrderUpdate(q, q_broker, OrderStatus.CANCELLED, 75, 2500990))
    assert w.engine.get(algo.algo_id).state is AlgoState.STOPPING  # hedges still work

    async def refuse(request):
        raise BrokerError("trading is stopped")

    w.broker.place = refuse  # the hedges are cancelled and can't go again (a kill switch)
    for leg in (NOV, DEC):
        for child in w.children(algo, leg):
            w.om.apply(OrderUpdate(child, w.om.get(child).broker_order_id, OrderStatus.CANCELLED))
    await w.engine.settle()
    view = w.engine.get(algo.algo_id)
    assert view.state.is_final
    assert f"UNHEDGED fly: 150 on {NOV}, 75 on {DEC}" in view.reason


async def test_a_fill_that_crosses_the_stop_is_still_hedged(w):
    algo = w.start(w.calendar(), -13000)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.engine.stop(algo.algo_id)
    w.fill(q, 150, 2501000)  # filled before the cancel landed
    await w.engine.settle()
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 150, 2514000)
    view = w.engine.get(algo.algo_id)
    assert view.state is AlgoState.STOPPED and "UNHEDGED" not in view.reason


async def test_a_kill_switch_stops_it_but_the_hedge_still_goes(w):
    algo = w.start(w.calendar(), -13000)
    await w.engine.settle()
    (q,) = w.children(algo)
    w.engine.stop_all("kill switch")
    w.fill(q, 150, 2501000)
    await w.engine.settle()
    assert w.placed()[-1] == ("NIFTY26NO", "SELL", 150, 2513980)  # risk-reducing: sent


# --- its definition -------------------------------------------------------------------------


async def test_it_works_the_definition_as_it_was_when_it_started(w):
    cal = w.calendar()
    algo = w.start(cal, -13000)
    await w.engine.settle()
    w.book.update(AYUSH, cal.spread_id, "now Oct/Dec",
                  [SpreadLeg(OCT, 1, 1, quote=True), SpreadLeg(DEC, -1, -1)])  # fmt: skip
    (q,) = w.children(algo)
    w.fill(q, 150, 2501000)
    await w.engine.settle()
    assert w.placed()[-1][0] == "NIFTY26NO"  # still hedged in NOV, as started


async def test_only_your_own_whole_spread_lots_on_its_quoting_leg(w):
    cal = w.calendar()
    other = w.start(cal, -13000, user="riya")
    wrong_leg = w.engine.start("ASE", user_id="ayush", instrument_id=NOV, side=Side.SELL, qty=150,
                               params={"spread": cal.spread_id, "level_paise": -13000})  # fmt: skip
    fly = w.butterfly(quote_on=NOV)  # one fly sells 2 lots of NOV: 1 lot is half a fly
    half = w.engine.start("ASE", user_id="ayush", instrument_id=NOV, side=Side.SELL, qty=75,
                          params={"spread": fly.spread_id, "level_paise": 0})  # fmt: skip
    await w.engine.settle()
    assert "Oct/Nov is not your spread" in w.engine.get(other.algo_id).reason
    assert f"the quoting leg is {OCT}, not {NOV}" in w.engine.get(wrong_leg.algo_id).reason
    assert "75 is not a whole number of spread lots (2 × 75" in w.engine.get(half.algo_id).reason
    with pytest.raises(AlgoError, match="there is no spread nope"):
        w.engine.start("ASE", user_id="ayush", instrument_id=OCT, side=Side.BUY, qty=150,
                       params={"spread": "nope", "level_paise": 0})  # fmt: skip
    assert w.broker.placed == []


# --- pieces that fit one order (#422) -------------------------------------------------------


def lots_cap(lots):
    """At most `lots` lots of 75 in one order, whatever the price (a dict: tests change it)."""
    cap = {"lots": lots, "asked": []}

    def order_cap(instrument_id, price):
        cap["asked"].append((instrument_id, price))
        return cap["lots"] * 75

    return cap, order_cap


async def test_a_hedge_bigger_than_one_order_goes_out_at_once_in_pieces(nifty_fut):
    cap, order_cap = lots_cap(10)
    w = W(nifty_fut, order_cap)
    algo = w.start(w.butterfly(), 0, lots=6)  # 6 lots of OCT; 12 lots of NOV to hedge
    await w.engine.settle()
    (q,) = w.children(algo)
    w.fill(q, 450, 2500990)
    await w.engine.settle()
    assert w.placed()[1:] == [("NIFTY26NO", "SELL", 750, 2513980),
                              ("NIFTY26NO", "SELL", 150, 2513980),
                              ("NIFTY26DE", "BUY", 450, 2527020)]  # fmt: skip
    assert (NOV, 2513980) in cap["asked"]  # the limit is asked at the price it goes at
    for c in w.children(algo, NOV):
        w.fill(c, w.om.get(c).request.qty, 2514000)
    w.engine.heartbeat()  # repeated updates never send a third NOV piece
    await w.engine.settle()
    assert len(w.children(algo, NOV)) == 2


async def test_a_hedge_that_no_longer_fits_says_so_and_sends_nothing(nifty_fut):
    cap, order_cap = lots_cap(10)
    w = W(nifty_fut, order_cap)
    algo = w.start(w.calendar(), -13000, lots=1)
    await w.engine.settle()
    (q,) = w.children(algo)
    cap["lots"] = 0  # the limits changed after the start: not one lot fits now
    w.fill(q, 75, 2501000)
    await w.engine.settle()
    view = w.engine.get(algo.algo_id)
    assert view.status == f"UNHEDGED 75 on {NOV}: one lot is over the per-order limit"
    assert w.children(algo, NOV) == [] and view.extra["unhedged"] == 75


async def test_the_quote_rests_one_piece_at_a_time_in_whole_spread_lots(nifty_fut):
    cap, order_cap = lots_cap(10)
    w = W(nifty_fut, order_cap)
    algo = w.start(w.calendar(), -13000, lots=15)
    await w.engine.settle()
    assert w.placed() == [("NIFTY26OC", "BUY", 750, 2501000)]  # 10 of 15 spread lots
    (q,) = w.children(algo)
    w.fill(q, 750, 2501000)
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.placed()[1:] == [("NIFTY26NO", "SELL", 750, 2513980)]  # hedged before more
    (hedge,) = w.children(algo, NOV)
    w.fill(hedge, 750, 2514000)
    cap["lots"] = 3  # the limits changed while it works: they are asked again each time
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.placed()[-1] == ("NIFTY26OC", "BUY", 225, 2501000)

    fly = w.butterfly(quote_on=NOV)  # one fly is 2 lots of NOV: 3 lots fit one fly only
    sold = w.start(fly, 10, lots=4)
    await w.engine.settle()
    (q,) = w.children(sold, NOV)
    assert w.om.get(q).request.qty == 150


async def test_a_resting_quote_too_big_at_its_new_price_is_pulled_and_quoted_smaller(nifty_fut):
    # a value limit: 2 lots fit at OCT's price now, 1 lot once it is above 25,050
    def order_cap(_instrument_id, price):
        return (1 if price and price > 2505000 else 2) * 75

    w = W(nifty_fut, order_cap)
    algo = w.start(w.calendar(), -13000, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo)
    assert w.om.get(q).request.qty == 150
    w.md.on_tick(quote(NOV, 2518010, 2518020))  # NOV up: the OCT quote goes to 25,050.10
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.broker.cancelled == [w.om.get(q).broker_order_id]  # pulled, not modified
    assert w.broker.modified == []
    view = w.om.get(q)
    w.om.apply(OrderUpdate(q, view.broker_order_id, OrderStatus.CANCELLED, 0, 0))
    w.engine.on_tick(w.md.latest(NOV))
    await w.engine.settle()
    assert w.placed()[-1] == ("NIFTY26OC", "BUY", 75, 2505010)


async def test_it_fails_at_start_in_words_if_one_lot_is_over_the_limit(nifty_fut):
    w = W(nifty_fut, lambda instrument_id, _price: 0 if instrument_id == NOV else 750)
    hedge_too_big = w.start(w.calendar(), -13000)
    w2 = W(nifty_fut, lambda _instrument_id, _price: 75)  # 1 lot: half of one fly's 2 NOV lots
    quote_too_big = w2.start(w2.butterfly(quote_on=NOV), 10, lots=1)
    await w.engine.settle()
    await w2.engine.settle()
    assert w.engine.get(hedge_too_big.algo_id).reason == (
        f"one lot of {NOV} is over this account's per-order limit")  # fmt: skip
    assert w2.engine.get(quote_too_big.algo_id).reason == (
        f"one spread lot (2 lots of {NOV}) is over this account's per-order limit")  # fmt: skip
    assert w.broker.placed == [] and w2.broker.placed == []


# --- end to end on the paper broker ---------------------------------------------------------


async def test_a_butterfly_on_the_paper_broker(nifty_fut):
    """Buy one OCT − 2·NOV + DEC at 0.00: the market reaches the OCT quote, it fills, and
    NOV and DEC fill at once; the account ends long 75 OCT, short 150 NOV, long 75 DEC."""
    w = W(nifty_fut)
    md, store = w.md, w.store
    md.on_tick(quote(OCT, 2501000, 2501010))  # OCT offered above our 25,009.90 quote
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"), paper, ids=ClientIds("MT"),
        limits=RiskLimits(max_order_value_paise=1_00_00_000_00),
        algo_types=(*BUILTIN, algo_type(w.book.get)),
    )  # fmt: skip
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        fly = w.butterfly()
        params = {"spread": fly.spread_id, "level_paise": 0}
        algo = runtime.algos.start("ASE", user_id="ayush", instrument_id=OCT, side=Side.BUY,
                                   qty=75, params=params)  # fmt: skip
        async with asyncio.timeout(5):
            while not runtime.orders.working():
                await asyncio.sleep(0.02)
        assert runtime.orders.working()[0].request.price_paise == 2500990
        md.on_tick(quote(OCT, 2500980, 2500990))  # OCT offered at our price: the quote fills
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.02)
        view = runtime.algos.get(algo.algo_id)
        assert view.extra["traded_spread_paise"] == 2500990 - 2 * 2514000 + 2527010
        held = [runtime.positions.net_qty(i) for i in (OCT, NOV, DEC)]
        assert held == [75, -150, 75]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()


async def test_on_the_paper_broker_every_order_fits_the_accounts_limits(nifty_fut):
    """The platform gives the algo the account's own limits (#422): ₹50 lakh fits 2 lots of
    OCT, so buying 3 calendars quotes 2, hedges 2, then quotes and hedges 1; nothing refused."""
    w = W(nifty_fut)
    md, store = w.md, w.store
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"), paper, ids=ClientIds("MT"),
        limits=RiskLimits(), algo_types=(*BUILTIN, algo_type(w.book.get)),
    )  # fmt: skip
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        cal = w.calendar()
        params = {"spread": cal.spread_id, "level_paise": -13000}
        algo = runtime.algos.start("ASE", user_id="ayush", instrument_id=OCT, side=Side.BUY,
                                   qty=225, params=params)  # fmt: skip
        async with asyncio.timeout(5):
            while not runtime.orders.working():
                await asyncio.sleep(0.02)
        assert runtime.orders.working()[0].request.qty == 150
        md.on_tick(quote(OCT, 2500000, 2501000))  # OCT offered at our quote: it fills
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.02)
                md.on_tick(quote(NOV, 2514000, 2514010))  # keep the legs fresh together
                md.on_tick(quote(OCT, 2500000, 2501000))
        orders = runtime.orders.orders()
        assert sorted((o.request.instrument_id, o.request.qty) for o in orders) == [
            (NOV, 75), (NOV, 150), (OCT, 75), (OCT, 150)]  # fmt: skip
        assert all(o.status is OrderStatus.FILLED for o in orders)
        assert [runtime.positions.net_qty(i) for i in (OCT, NOV)] == [225, -225]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()
