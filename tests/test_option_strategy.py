"""Strategies: templates around the ATM, exact expiry payoff maths, today's curve, validation."""

from datetime import date, datetime

import pytest

from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.options.chain import ChainService
from mocktrade.options.model import IST, time_to_expiry
from mocktrade.options.pricing import black76
from mocktrade.options.strategy import TEMPLATES, Strategies, StrategyError, templates
from mocktrade.refdata.store import InstrumentStore

K = InstrumentKind
EXP, NOV = date(2026, 10, 27), date(2026, 11, 24)
NOW = datetime(2026, 10, 20, 11, 0, tzinfo=IST)
STRIKES = range(24400, 25601, 100)
BANK = "BANKNIFTY26OCT56000CE"


def option(strike, kind, expiry=EXP):
    code = "26OCT" if expiry == EXP else "26NOV"
    symbol = f"NIFTY{code}{strike}{kind.value}"
    return Instrument(Exchange.NFO, symbol, kind, 5, 75, expiry, strike * 100, "NIFTY")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def world():
    store, md, clock = InstrumentStore(), MarketData(), Clock(NOW)
    for expiry, fut in ((EXP, "NIFTY26OCTFUT"), (NOV, "NIFTY26NOVFUT")):
        store.add(Instrument(Exchange.NFO, fut, K.FUTURE, 10, 75, expiry, None, "NIFTY"))
        for k in STRIKES:
            for kind in (K.CALL, K.PUT):
                store.add(option(k, kind, expiry))
    store.add(Instrument(Exchange.NFO, BANK, K.CALL, 5, 35, EXP, 5600000, "BANKNIFTY"))

    def price(forward=25000.0, sigma=0.13):
        futures = ((EXP, "NFO:NIFTY26OCTFUT", forward), (NOV, "NFO:NIFTY26NOVFUT", forward + 140))
        for expiry, fut, f in futures:
            md.on_tick(Tick(fut, round(f * 100)))
            T = time_to_expiry(expiry, clock())
            for k in STRIKES:
                for kind in (K.CALL, K.PUT):
                    p = round(black76(f, k, T, 0.065, sigma, kind is K.CALL) * 20) * 5  # tick grid
                    p = max(p, 5)
                    levels = dict(bids=(DepthLevel(p, 75, 1),), asks=(DepthLevel(p, 75, 1),))
                    md.on_tick(Tick(option(k, kind, expiry).id, p, **levels))

    price()
    chains = ChainService(store, md, clock=clock)
    return Strategies(chains, clock=clock), md, clock, price


def leg(strike, kind, side, lots=1, price=None, expiry=EXP):
    d = {"instrument_id": option(strike, kind, expiry).id, "side": side, "lots": lots}
    if price is not None:
        d["price_paise"] = price
    return d


# --- templates ---


def test_every_template_is_published_with_its_parameters():
    published = templates()
    assert [t["code"] for t in published] == [t.code for t in TEMPLATES]
    assert all({p["name"] for p in t["params"]} == {"width", "lots"} for t in published)


CONDOR = [("BUY", 24800, "PE"), ("SELL", 24900, "PE"), ("SELL", 25100, "CE"), ("BUY", 25200, "CE")]


@pytest.mark.parametrize(
    "code, expected",
    [
        ("IRON_CONDOR", CONDOR),
        ("CALL_BUTTERFLY", [("BUY", 24900, "CE"), ("SELL", 25000, "CE"), ("BUY", 25100, "CE")]),
        ("LONG_STRANGLE", [("BUY", 25100, "CE"), ("BUY", 24900, "PE")]),
    ],
)
def test_templates_build_around_the_atm(world, code, expected):
    s, *_ = world
    legs = s.build(code, "NIFTY", EXP, width=1, lots=2)
    got = [(lg["side"], int(lg["instrument_id"][-7:-2]), lg["instrument_id"][-2:]) for lg in legs]
    assert got == expected
    if code == "CALL_BUTTERFLY":
        assert [lg["lots"] for lg in legs] == [2, 4, 2]  # the body is two lots per strategy lot


def test_a_template_wider_than_the_listed_strikes_is_refused(world):
    s, *_ = world
    with pytest.raises(StrategyError, match="needs strikes not listed"):
        s.build("IRON_CONDOR", "NIFTY", EXP, width=4, lots=1)
    with pytest.raises(StrategyError, match="no strategy"):
        s.build("MOON_SHOT", "NIFTY", EXP, width=1, lots=1)


# --- exact maths at expiry, by hand ---


def test_iron_condor_by_hand(world):
    s, *_ = world
    # credit: sell 24900 PE @ 40, 25100 CE @ 45; buy 24800 PE @ 20, 25200 CE @ 25 -> 40 a unit
    legs = [
        leg(24800, K.PUT, "BUY", price=2000),
        leg(24900, K.PUT, "SELL", price=4000),
        leg(25100, K.CALL, "SELL", price=4500),
        leg(25200, K.CALL, "BUY", price=2500),
    ]
    a = s.analyse(legs)
    assert a["net_premium"] == 40 * 75
    assert a["max_profit"] == 40 * 75
    assert a["max_loss"] == -(100 - 40) * 75  # width 100 minus the credit
    assert a["breakevens"] == [24860.0, 25140.0]


def test_straddle_and_ratio_spread_unlimited_sides(world):
    s, *_ = world
    straddle = s.analyse(
        [leg(25000, K.CALL, "BUY", price=20000), leg(25000, K.PUT, "BUY", price=19000)]
    )
    assert straddle["max_profit"] == "unlimited" and straddle["max_loss"] == -390 * 75
    assert straddle["breakevens"] == [24610.0, 25390.0]
    ratio = s.analyse(
        [leg(25000, K.CALL, "BUY", price=20000), leg(25200, K.CALL, "SELL", 2, price=11000)]
    )
    assert ratio["max_loss"] == "unlimited"
    assert ratio["net_premium"] == (2 * 110 - 200) * 75  # a small credit
    assert ratio["max_profit"] == (200 + 20) * 75  # at 25,200
    assert ratio["breakevens"] == [25420.0]


def test_butterfly_peaks_at_the_body(world):
    s, *_ = world
    fly = s.analyse(
        [
            leg(24900, K.CALL, "BUY", price=15000),
            leg(25000, K.CALL, "SELL", 2, price=10000),
            leg(25100, K.CALL, "BUY", price=6000),
        ]
    )
    debit = (150 - 200 + 60) * 75
    assert fly["net_premium"] == -debit
    assert fly["max_profit"] == 100 * 75 - debit and fly["max_loss"] == -debit


# --- today's curve, Greeks, calendars ---


def test_the_today_curve_meets_the_expiry_payoff_at_expiry(world):
    s, md, clock, price = world
    clock.now = datetime(2026, 10, 27, 15, 29, 59, 900000, tzinfo=IST)  # 0.1 s left
    price(sigma=0.13)
    a = s.analyse([leg(25000, K.CALL, "BUY", price=20000), leg(25200, K.CALL, "SELL", price=11000)])
    for e, t in zip(a["payoff_expiry"], a["payoff_today"], strict=True):
        assert t == pytest.approx(e, abs=5)


def test_net_greeks_add_up_by_units(world):
    s, *_ = world
    one = s.analyse([leg(25000, K.CALL, "BUY")])["net_greeks"]
    two = s.analyse([leg(25000, K.CALL, "BUY", lots=2)])["net_greeks"]
    assert two["delta"] == pytest.approx(2 * one["delta"], rel=1e-6)
    assert 0.45 * 75 < one["delta"] < 0.6 * 75  # per rupee of the future, whole position
    short = s.analyse([leg(25000, K.CALL, "SELL")])["net_greeks"]
    assert short["theta"] > 0 > one["theta"]


def test_a_calendar_is_valued_at_the_near_expiry_with_a_curve(world):
    s, *_ = world
    a = s.analyse([leg(25000, K.CALL, "SELL"), leg(25000, K.CALL, "BUY", expiry=NOV)])
    assert a["expiry"] == EXP.isoformat()
    assert a["max_profit"] != "unlimited" and a["max_loss"] != "unlimited"
    assert len(a["breakevens"]) == 2  # profit in a band around the strike
    peak = a["prices"][a["payoff_expiry"].index(max(a["payoff_expiry"]))]
    assert abs(peak - 25000) < 150


# --- what is refused ---


def test_bad_legs_are_refused_with_a_reason(world):
    s, md, *_ = world
    bank = {"instrument_id": "NFO:" + BANK, "side": "BUY", "lots": 1}
    with pytest.raises(StrategyError, match="not a listed option"):
        s.analyse([{"instrument_id": "NFO:NIFTY26OCTFUT", "side": "BUY", "lots": 1}])
    with pytest.raises(StrategyError, match="no price yet"):
        s.analyse([bank])
    md.on_tick(Tick("NFO:" + BANK, 30000))
    with pytest.raises(StrategyError, match="same underlying"):
        s.analyse([leg(25000, K.CALL, "BUY"), bank])
    with pytest.raises(StrategyError, match="1 to 8 legs"):
        s.analyse([leg(25000, K.CALL, "BUY")] * 9)
    with pytest.raises(StrategyError, match="side must be"):
        s.analyse([leg(25000, K.CALL, "HOLD")])
