"""Multi-leg spreads and their live implied prices (#414), worked by hand.

calendar  NOV − DEC        buy it = buy NOV at its ask, sell DEC at its bid
butterfly A − 2B + C       buy it = buy A and C at their asks, sell 2 B at its bid
"""

from datetime import UTC, date, datetime

import pytest

from laddrix.domain.accounts import User
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.marketdata.service import MarketData
from laddrix.spreads.model import (
    MAX_SPREADS_PER_USER,
    SpreadDef,
    SpreadLeg,
    check,
    implied,
    spread_from_dict,
    spread_tick,
    spread_to_dict,
)
from laddrix.spreads.pricer import SpreadPricer
from laddrix.spreads.store import SpreadBook, SpreadError

NOV, DEC, JAN = "NFO:NIFTY26NOVFUT", "NFO:NIFTY26DECFUT", "NFO:NIFTY27JANFUT"
SBIN, BANK = "NSE:SBIN", "NFO:BANKNIFTY26NOVFUT"
T0 = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)


def fut(symbol: str, tick: int = 10, lot: int = 75) -> Instrument:
    return Instrument(Exchange.NFO, symbol, InstrumentKind.FUTURE, tick_paise=tick, lot_size=lot,
                      expiry=date(2026, 11, 25), underlying="NIFTY")  # fmt: skip


INSTRUMENTS = {
    NOV: fut("NIFTY26NOVFUT"),
    DEC: fut("NIFTY26DECFUT"),
    JAN: fut("NIFTY27JANFUT"),
    BANK: fut("BANKNIFTY26NOVFUT", tick=20, lot=35),
    SBIN: Instrument(Exchange.NSE, "SBIN", InstrumentKind.EQUITY, tick_paise=5, lot_size=1),
    "NSE:NIFTY 50": Instrument(Exchange.NSE, "NIFTY 50", InstrumentKind.INDEX, 5, 1),
}
lookup = INSTRUMENTS.get
AYUSH = User("ayush", "Ayush")


def tick(i: str, bid: int, bid_qty: int, ask: int, ask_qty: int, ltp: int | None = None) -> Tick:
    bids, asks = (DepthLevel(bid, bid_qty, 1),), (DepthLevel(ask, ask_qty, 1),)
    return Tick(i, ltp if ltp is not None else bid, received_at=T0, bids=bids, asks=asks)


def calendar(**quote) -> list[SpreadLeg]:
    return [SpreadLeg(NOV, 1, 1, quote=True), SpreadLeg(DEC, -1, -1)]


def spread(legs: list[SpreadLeg], name: str = "cal") -> SpreadDef:
    return SpreadDef("s1", "ayush", "main", name, tuple(legs), T0, T0)


# --- implied prices -------------------------------------------------------------------------


def test_a_calendar_buys_one_leg_at_its_ask_and_sells_the_other_at_its_bid():
    prices = {NOV: tick(NOV, 2500000, 750, 2500100, 300, ltp=2500050),
              DEC: tick(DEC, 2515000, 150, 2515200, 900, ltp=2515100)}  # fmt: skip
    now = implied(spread(calendar()), prices.get, lookup)
    # buy the spread: NOV ask 25001.00 − DEC bid 25150.00 = −149.00; lots: min(300/75, 150/75)
    assert now.ask == DepthLevel(2500100 - 2515000, 2, 0)
    # sell it: NOV bid 25000.00 − DEC ask 25152.00 = −152.00; lots: min(750/75, 900/75)
    assert now.bid == DepthLevel(2500000 - 2515200, 10, 0)
    assert now.ltp_paise == 2500050 - 2515100 and now.stale == ""
    assert now.bid.price_paise < now.ask.price_paise  # selling is never dearer than buying


def test_a_butterfly_weighs_its_middle_leg_twice_in_price_and_in_size():
    legs = [SpreadLeg(NOV, 1, 1, quote=True), SpreadLeg(DEC, -2, -2), SpreadLeg(JAN, 1, 1)]
    prices = {NOV: tick(NOV, 100000, 750, 100010, 750),
              DEC: tick(DEC, 101000, 225, 101020, 3000),  # 225 = 3 lots: 1 butterfly needs 2
              JAN: tick(JAN, 102000, 750, 102030, 750)}  # fmt: skip
    now = implied(spread(legs), prices.get, lookup)
    assert now.ask == DepthLevel(100010 - 2 * 101000 + 102030, 1, 0)  # floor(3 lots / 2)
    assert now.bid == DepthLevel(100000 - 2 * 101020 + 102000, 10, 0)  # min(10, 3000/150, 10)


def test_legs_of_different_lot_sizes_count_in_their_own_lots():
    legs = [SpreadLeg(NOV, 1, 1, quote=True), SpreadLeg(BANK, -1, -1)]
    prices = {
        NOV: tick(NOV, 2500000, 750, 2500100, 750),  # 10 lots of 75
        BANK: tick(BANK, 5600000, 70, 5600020, 700),
    }  # 2 lots of 35 on the bid
    assert implied(spread(legs), prices.get, lookup).ask.qty == 2


def test_no_price_on_a_side_whose_leg_has_none_or_less_than_a_lot():
    prices = {NOV: Tick(NOV, 2500000, received_at=T0, asks=(DepthLevel(2500100, 300, 1),)),
              DEC: tick(DEC, 2515000, 50, 2515200, 900)}  # fmt: skip
    now = implied(spread(calendar()), prices.get, lookup)
    assert now.ask is None  # DEC's bid holds 50 of a 75 lot
    assert now.bid is None  # NOV has no bid
    missing = implied(spread(calendar()), {NOV: prices[NOV]}.get, lookup)
    assert (missing.bid, missing.ask, missing.ltp_paise) == (None, None, None)


def test_legs_priced_far_apart_in_time_give_no_tradable_price():
    prices = {NOV: tick(NOV, 2500000, 750, 2500100, 750),
              DEC: tick(DEC, 2515000, 750, 2515200, 750)}  # fmt: skip
    ages = {NOV: 0.2, DEC: 4.0}
    now = implied(spread(calendar()), prices.get, lookup, ages.get)
    assert (now.bid, now.ask) == (None, None) and now.stale == "leg prices 4 s apart"
    assert now.ltp_paise == 2500000 - 2515000  # still marked, never traded on
    fresh = implied(spread(calendar()), prices.get, lookup, {NOV: 0.5, DEC: 3.5}.get)
    assert fresh.ask is not None  # exactly 3 s apart: still one moment


def test_the_spread_tick_is_the_step_every_leg_moving_one_tick_makes():
    assert spread_tick(tuple(calendar()), lookup) == 10
    assert spread_tick((SpreadLeg(NOV, 1, 1), SpreadLeg(DEC, -2, -2)), lookup) == 10  # 10, 20
    assert spread_tick((SpreadLeg(NOV, 1, 1), SpreadLeg(SBIN, -1, -3)), lookup) == 5  # 10, 15
    assert spread_tick((SpreadLeg(BANK, 1, 2), SpreadLeg(SBIN, -1, -2)), lookup) == 10  # 40, 10


# --- what a definition may be ---------------------------------------------------------------


Q = SpreadLeg(NOV, 1, 1, quote=True)  # the quoted leg most cases share


def leg(i: str, ratio: int, mult: int, **kw) -> SpreadLeg:
    return SpreadLeg(i, ratio, mult, **kw)


WHOLE = "the ratio must be a whole number from 1 to 50"


@pytest.mark.parametrize(("name", "legs", "problem"), [
    ("", calendar(), "the name must be 1 to 40 characters"),
    ("x" * 41, calendar(), "the name must be 1 to 40 characters"),
    ("one", [Q], "a spread has 2 to 6 legs"),
    ("seven", [Q] + [leg(f"NFO:X{i}", 1, 1) for i in range(6)], "a spread has 2 to 6 legs"),
    ("same", [Q, leg(NOV, -1, -1)], "each leg must be a different contract"),
    ("unknown", [Q, leg("NFO:NOPE", -1, -1)], "leg 2: NFO:NOPE is not a known contract"),
    ("index", [Q, leg("NSE:NIFTY 50", -1, -1)], "leg 2: NSE:NIFTY 50 can't be traded"),
    ("zero", [leg(NOV, 0, 1, quote=True), leg(DEC, -1, -1)], f"leg 1: {WHOLE}"),
    ("big", [leg(NOV, 51, 1, quote=True), leg(DEC, -1, -1)], f"leg 1: {WHOLE}"),
    ("mult", [leg(NOV, 1, 0, quote=True), leg(DEC, -1, -1)], "leg 1: the price multiplier must"),
    ("sign", [Q, leg(DEC, -1, 1)], "leg 2: the price multiplier must have the ratio's sign"),
    ("payup", [Q, leg(DEC, -1, -1, payup_ticks=51)], "leg 2: pay-up must be 0 to 50 ticks"),
    ("none", [leg(NOV, 1, 1), leg(DEC, -1, -1)], "choose at least one leg to quote"),
])  # fmt: skip
def test_every_problem_is_said_in_words(name, legs, problem):
    found = check(name, legs, lookup)
    assert any(p.startswith(problem) for p in found), found


def test_a_good_definition_has_no_problems_and_an_expired_leg_does():
    assert check("NIFTY Nov/Dec", calendar(), lookup) == []
    assert check("both", [Q, leg(DEC, -1, -1, quote=True)], lookup) == []  # #427
    assert check("cal", calendar(), lookup, expired=lambda i: i.symbol == "NIFTY26DECFUT") == [
        "leg 2: NFO:NIFTY26DECFUT has expired"
    ]


# --- the book: per user, kept on disk -------------------------------------------------------


def test_spreads_are_kept_across_restarts_and_seen_only_by_their_owner(tmp_path):
    book = SpreadBook(tmp_path, lookup, clock=lambda: T0)
    made = book.create(AYUSH, "  NIFTY Nov/Dec ", calendar())
    assert made.name == "NIFTY Nov/Dec" and made.instrument_id == f"SPREAD:{made.spread_id}"
    book.create(User("ravi", "Ravi"), "his", calendar())
    book.write("ayush")
    book.write("ravi")
    assert book.path_of("ayush").read_text().count('"spread_id"') == 1  # only theirs in each file
    again = SpreadBook(tmp_path, lookup)
    again.load()
    assert again.mine(AYUSH) == [made]
    assert again.visible(AYUSH, made.instrument_id) == made
    assert again.visible(User("ravi", "Ravi"), made.instrument_id) is None  # someone else
    assert again.visible(User("ayush", "Ayush", workspace="acme"), made.instrument_id) is None
    assert [s.name for s in again.mine(User("ravi", "Ravi"))] == ["his"]  # his own, from his file
    assert spread_from_dict(spread_to_dict(made)) == made


def test_changes_are_checked_and_only_the_owner_changes_them(tmp_path):
    times = iter([T0, T0.replace(hour=10)])
    book = SpreadBook(tmp_path, lookup, clock=lambda: next(times))
    made = book.create(AYUSH, "cal", calendar())
    with pytest.raises(SpreadError) as bad:
        book.update(AYUSH, made.spread_id, "cal", [SpreadLeg(NOV, 1, 1), SpreadLeg(DEC, -1, -1)])
    assert bad.value.problems[0] == "choose at least one leg to quote" and not bad.value.missing
    with pytest.raises(SpreadError) as other:
        book.update(User("ravi", "Ravi"), made.spread_id, "mine now", calendar())
    assert other.value.missing
    with pytest.raises(SpreadError):
        book.delete(User("ravi", "Ravi"), made.spread_id)
    changed = book.update(AYUSH, made.spread_id, "fly", calendar() + [SpreadLeg(JAN, 1, 1)])
    assert changed.name == "fly" and len(changed.legs) == 3
    assert changed.created_at == made.created_at == T0  # made then; changed later
    assert changed.updated_at == T0.replace(hour=10)
    book.delete(AYUSH, made.spread_id)
    assert book.mine(AYUSH) == []


def test_a_user_can_keep_at_most_fifty(tmp_path):
    book = SpreadBook(None, lookup)
    for n in range(MAX_SPREADS_PER_USER):
        book.create(AYUSH, f"s{n}", calendar())
    with pytest.raises(SpreadError, match="at most 50 spreads"):
        book.create(AYUSH, "one more", calendar())
    book.create(User("ravi", "Ravi"), "his own", calendar())  # each user's own fifty


# --- live prices ----------------------------------------------------------------------------


def test_a_watched_spread_is_priced_from_its_legs_on_every_tick():
    md = MarketData()
    book = SpreadBook(None, lookup)
    pricer = SpreadPricer(md, book, lookup)
    made = book.create(AYUSH, "cal", calendar())
    assert pricer.pricing() == set()  # nobody watches it: nothing priced
    screen = md.subscribe([made.instrument_id])
    assert md.is_watched(NOV) and md.is_watched(DEC)  # its legs stream now
    md.on_tick(tick(NOV, 2500000, 750, 2500100, 750))
    md.on_tick(tick(DEC, 2515000, 750, 2515200, 750))
    seen = [t for t in screen.drain() if t.instrument_id == made.instrument_id]
    assert seen[-1].asks[0].price_paise == 2500100 - 2515000
    assert seen[-1].bids[0].price_paise == 2500000 - 2515200
    md.on_tick(tick(DEC, 2514000, 750, 2514200, 750))  # DEC falls 10 rupees: the spread rises
    assert screen.drain()[-1].asks[0].price_paise == 2500100 - 2514000


def test_derived_prices_never_reach_the_recorder_paper_fills_or_charts():
    md = MarketData()
    book = SpreadBook(None, lookup)
    SpreadPricer(md, book, lookup)
    made = book.create(AYUSH, "cal", calendar())
    tapped = []
    md.tap(tapped.append)
    md.subscribe([made.instrument_id])
    md.on_tick(tick(NOV, 2500000, 750, 2500100, 750))
    md.on_tick(tick(DEC, 2515000, 750, 2515200, 750))
    assert [t.instrument_id for t in tapped] == [NOV, DEC]  # only the market's own
    assert md.latest(made.instrument_id) is not None and md.ticks_received == 2


def test_an_edited_spread_is_priced_from_its_new_legs_and_a_deleted_one_goes_blank():
    md = MarketData()
    book = SpreadBook(None, lookup)
    pricer = SpreadPricer(md, book, lookup)
    made = book.create(AYUSH, "cal", calendar())
    screen = md.subscribe([made.instrument_id])
    for t in (tick(NOV, 2500000, 750, 2500100, 750), tick(DEC, 2515000, 750, 2515200, 750),
              tick(JAN, 2530000, 750, 2530200, 750)):  # fmt: skip
        md.on_tick(t)
    book.update(AYUSH, made.spread_id, "Nov/Jan", [Q, SpreadLeg(JAN, -1, -1)])
    assert md.is_watched(JAN) and not md.is_watched(DEC)  # the old leg isn't streamed for it
    assert screen.drain()[-1].asks[0].price_paise == 2500100 - 2530000  # at once, from JAN
    md.on_tick(tick(DEC, 1, 750, 2, 750))  # DEC no longer counts
    assert md.latest(made.instrument_id).asks[0].price_paise == 2500100 - 2530000
    book.delete(AYUSH, made.spread_id)
    assert pricer.pricing() == set()
    blank = screen.drain()[-1]
    assert (blank.bids, blank.asks) == ((), ())
