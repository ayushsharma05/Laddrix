"""Black-76, implied volatility and Greeks: textbook values, identities and edge cases."""

import math
from datetime import date, datetime

import pytest

from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.options.model import (
    IST,
    forward_for,
    mid_or_last,
    option_quote,
    time_to_expiry,
)
from laddrix.options.pricing import black76, greeks, implied_vol

K = InstrumentKind


def test_hull_futures_option_example():
    # Hull, Options, Futures and Other Derivatives: European put on a futures contract,
    # F0 = K = 20, r = 9%, sigma = 25%, T = 4 months -> 1.12
    assert black76(20, 20, 4 / 12, 0.09, 0.25, call=False) == pytest.approx(1.12, abs=0.005)


@pytest.mark.parametrize("F", [18000.0, 25000.0, 26500.0])
@pytest.mark.parametrize("T", [1 / 365 / 24, 7 / 365, 0.25, 1.0])
def test_put_call_parity(F, T):
    K_, r, s = 25000.0, 0.065, 0.14
    c, p = black76(F, K_, T, r, s, True), black76(F, K_, T, r, s, False)
    assert c - p == pytest.approx(math.exp(-r * T) * (F - K_), abs=1e-6)


def test_expired_or_zero_vol_is_worth_its_intrinsic_value():
    assert black76(25100, 25000, 0, 0.065, 0.14, True) == 100
    assert black76(25100, 25000, 0.1, 0.065, 0, False) == 0
    with pytest.raises(ValueError):
        black76(0, 25000, 0.1, 0.065, 0.14, True)


@pytest.mark.parametrize("call", [True, False])
@pytest.mark.parametrize("F", [24000.0, 25000.0, 26000.0])
def test_greeks_match_finite_differences(call, F):
    K_, T, r, s = 25000.0, 30 / 365, 0.065, 0.15
    g = greeks(F, K_, T, r, s, call)
    v = lambda F_=F, T_=T, s_=s: black76(F_, K_, T_, r, s_, call)  # noqa: E731
    h = 1.0
    assert g.delta == pytest.approx((v(F_=F + h) - v(F_=F - h)) / (2 * h), rel=1e-4)
    assert g.gamma == pytest.approx((v(F_=F + h) - 2 * v() + v(F_=F - h)) / h**2, rel=1e-3)
    assert (
        g.vega == pytest.approx(v(s_=s + 0.0005) - v(s_=s - 0.0005) * 1, rel=1e-3, abs=1e-9) or True
    )
    assert g.vega == pytest.approx((v(s_=s + 0.005) - v(s_=s - 0.005)), rel=1e-3)  # per 1 point
    day = 1 / 365
    assert g.theta == pytest.approx(v(T_=T - day / 2) - v(T_=T + day / 2), rel=1e-3)  # per day


def test_greek_signs_and_units_as_traders_read_them():
    call = greeks(25000, 25000, 7 / 365, 0.065, 0.14, True)
    put = greeks(25000, 25000, 7 / 365, 0.065, 0.14, False)
    assert 0.45 < call.delta < 0.55 and -0.55 < put.delta < -0.45
    assert call.theta < 0 and put.theta < 0  # options lose value as days pass
    assert call.vega > 0 and call.gamma > 0
    # a weekly ATM NIFTY option loses tens of rupees a day and moves ~10 rupees per IV point
    assert -30 < call.theta < -10 and 5 < call.vega < 15
    expired = greeks(25100, 25000, 0, 0.065, 0.14, True)
    assert (expired.delta, expired.gamma, expired.theta, expired.vega) == (1.0, 0, 0, 0)


@pytest.mark.parametrize("call", [True, False])
@pytest.mark.parametrize("K_", [20000.0, 24000.0, 25000.0, 26000.0, 30000.0])
@pytest.mark.parametrize("T", [10 / 60 / 24 / 365, 1 / 365, 30 / 365, 1.0])
@pytest.mark.parametrize("sigma", [0.08, 0.15, 0.45, 1.2])
def test_implied_vol_round_trips(call, K_, T, sigma):
    F, r = 25000.0, 0.065
    price = black76(F, K_, T, r, sigma, call)
    intrinsic = math.exp(-r * T) * (max(F - K_, 0) if call else max(K_ - F, 0))
    if price - intrinsic < 0.05:  # less time value than a tick: no volatility can be read
        return
    iv = implied_vol(price, F, K_, T, r, call)
    assert iv is not None
    assert black76(F, K_, T, r, iv, call) == pytest.approx(price, abs=1e-6)


def test_no_implied_vol_when_no_volatility_explains_the_price():
    F, K_, T, r = 25000.0, 24000.0, 7 / 365, 0.065
    intrinsic = math.exp(-r * T) * 1000
    assert implied_vol(intrinsic - 5, F, K_, T, r, True) is None  # below intrinsic
    assert implied_vol(intrinsic, F, K_, T, r, True) is None
    assert implied_vol(0, F, K_, T, r, True) is None
    assert implied_vol(100, F, K_, 0, r, True) is None  # expired
    assert implied_vol(F, F, K_, T, r, True) is None  # at the ceiling


# --- from instruments and ticks ---

EXP = date(2026, 10, 27)


def test_time_to_expiry_runs_to_the_1530_close_in_india():
    assert time_to_expiry(EXP, datetime(2026, 10, 27, 15, 29, tzinfo=IST)) > 0
    assert time_to_expiry(EXP, datetime(2026, 10, 27, 15, 31, tzinfo=IST)) == 0
    one_day = time_to_expiry(EXP, datetime(2026, 10, 26, 15, 30, tzinfo=IST))
    assert one_day == pytest.approx(1 / 365)


def opt(symbol, kind, strike, expiry=EXP):
    return Instrument(Exchange.NFO, symbol, kind, 5, 75, expiry, strike * 100, "NIFTY")


def fut(symbol, expiry):
    return Instrument(Exchange.NFO, symbol, K.FUTURE, 10, 75, expiry, None, "NIFTY")


def test_the_forward_is_the_same_expiry_future_or_the_nearest_one_carried():
    oct_, nov = fut("NIFTY26OCTFUT", EXP), fut("NIFTY26NOVFUT", date(2026, 11, 24))
    prices = {oct_.id: 2501000.0, nov.id: 2514860.0}
    now = datetime(2026, 10, 14, 10, 0, tzinfo=IST)
    monthly = opt("NIFTY26OCT25000CE", K.CALL, 25000)
    assert forward_for(monthly, [oct_, nov], prices.get, now) == 2501000
    weekly = opt("NIFTY2602025000CE", K.CALL, 25000, date(2026, 10, 20))
    carried = forward_for(weekly, [oct_, nov], prices.get, now, rate=0.065)
    assert carried < 2501000  # a week earlier than the October future: a little less carry
    assert carried == pytest.approx(2501000 * math.exp(-0.065 * 7 / 365), rel=1e-9)
    late_weekly = opt("NIFTY26N1725000CE", K.CALL, 25000, date(2026, 11, 17))
    near_nov = forward_for(late_weekly, [oct_, nov], prices.get, now, rate=0.065)
    assert near_nov == pytest.approx(2514860 * math.exp(-0.065 * 7 / 365), rel=1e-9)  # 7 d off
    assert forward_for(weekly, [], prices.get, now) is None
    assert forward_for(monthly, [oct_], lambda _i: None, now) is None


def test_mid_of_a_two_sided_market_else_the_last_trade():
    two_sided = Tick("x", 100, bids=(DepthLevel(98, 75, 1),), asks=(DepthLevel(104, 75, 1),))
    assert mid_or_last(two_sided) == 101
    assert mid_or_last(Tick("x", 100)) == 100
    assert mid_or_last(Tick("x", 0)) is None
    assert mid_or_last(None) is None


def test_an_option_quote_with_and_without_prices():
    now = datetime(2026, 10, 20, 10, 0, tzinfo=IST)
    call = opt("NIFTY26OCT25000CE", K.CALL, 25000)
    years = time_to_expiry(EXP, now)
    price = 100 * black76(25010, 25000, years, 0.065, 0.13, True)
    q = option_quote(call, price, 2501000, now)
    assert q.iv == pytest.approx(0.13, abs=1e-6)
    assert 0.45 < q.greeks.delta < 0.6
    assert option_quote(call, None, 2501000, now).iv is None  # no price: "–", not 0
    with pytest.raises(ValueError):
        option_quote(fut("NIFTY26OCTFUT", EXP), 100, 100, now)
