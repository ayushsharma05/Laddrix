"""The option chain: strikes around ATM, IV and Greeks per row, PCR, max pain, streaming set."""

from datetime import date, datetime

import pytest

from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.options.chain import ChainService, max_pain
from mocktrade.options.model import IST, time_to_expiry
from mocktrade.options.pricing import black76
from mocktrade.refdata.store import InstrumentStore

K = InstrumentKind
EXP = date(2026, 10, 27)
NOW = datetime(2026, 10, 20, 11, 0, tzinfo=IST)
FUT = Instrument(Exchange.NFO, "NIFTY26OCTFUT", K.FUTURE, 10, 75, EXP, None, "NIFTY")
STRIKES = range(24600, 25401, 100)


def option(strike, kind):
    return Instrument(
        Exchange.NFO, f"NIFTY26OCT{strike}{kind.value}", kind, 5, 75, EXP, strike * 100, "NIFTY"
    )


def tick(instrument_id, paise, oi=None):
    p = round(paise / 5) * 5
    return Tick(
        instrument_id,
        p,
        open_interest=oi,
        bids=(DepthLevel(p - 5, 75, 1),),
        asks=(DepthLevel(p + 5, 75, 1),),
    )


@pytest.fixture
def world():
    store, md = InstrumentStore(), MarketData()
    store.add(FUT)
    for k in STRIKES:
        for kind in (K.CALL, K.PUT):
            store.add(option(k, kind))
    # a past expiry and another underlying's options are never in this chain
    store.add(
        Instrument(
            Exchange.NFO, "NIFTY26SEP25000CE", K.CALL, 5, 75, date(2026, 9, 29), 2500000, "NIFTY"
        )
    )
    store.add(
        Instrument(Exchange.NFO, "BANKNIFTY26OCT56000CE", K.CALL, 5, 35, EXP, 5600000, "BANKNIFTY")
    )
    service = ChainService(store, md, clock=lambda: NOW)

    def price_all(forward=25010.0, sigma=0.13, oi=None):
        md.on_tick(Tick(FUT.id, round(forward * 100)))
        T = time_to_expiry(EXP, NOW)
        for k in STRIKES:
            for kind in (K.CALL, K.PUT):
                value = black76(forward, k, T, 0.065, sigma, kind is K.CALL) * 100
                o = (oi or {}).get((k, kind))
                md.on_tick(tick(option(k, kind).id, value, o))

    return service, md, price_all


def test_what_is_listed(world):
    service, _, _ = world
    assert service.underlyings() == ["BANKNIFTY", "NIFTY"]
    assert service.expiries("NIFTY") == [EXP]  # September has expired


def test_rows_around_atm_with_iv_and_greeks(world):
    service, _, price_all = world
    price_all()
    snap = service.snapshot("NIFTY", EXP, around=2)
    assert snap["forward_paise"] == 2501000 and snap["atm_strike_paise"] == 2500000
    assert [r["strike_paise"] for r in snap["rows"]] == [
        2480000,
        2490000,
        2500000,
        2510000,
        2520000,
    ]
    atm = snap["rows"][2]
    # prices were made at 13% vol; the tick grid and the mid move IV only slightly
    assert atm["call"]["iv"] == pytest.approx(0.13, abs=0.002)
    assert atm["put"]["iv"] == pytest.approx(0.13, abs=0.002)
    assert snap["atm_iv"] == pytest.approx(0.13, abs=0.002)
    assert 0.45 < atm["call"]["delta"] < 0.6 and -0.55 < atm["put"]["delta"] < -0.4
    assert atm["call"]["theta"] < 0 and atm["call"]["vega"] > 0
    assert snap["days_to_expiry"] == pytest.approx(7.19, abs=0.01)


def test_no_price_means_no_iv_never_a_zero(world):
    service, md, _ = world
    md.on_tick(Tick(FUT.id, 2501000))
    row = service.snapshot("NIFTY", EXP, around=0)["rows"][0]
    assert row["call"]["ltp_paise"] is None and row["call"]["iv"] is None
    assert row["call"]["delta"] is None


def test_without_the_future_there_is_no_atm_and_the_middle_strikes_show():
    store, md = InstrumentStore(), MarketData()
    for k in STRIKES:
        store.add(option(k, K.CALL))
    snap = ChainService(store, md, clock=lambda: NOW).snapshot("NIFTY", EXP, around=1)
    assert snap["forward_paise"] is None and snap["atm_strike_paise"] is None
    assert [r["strike_paise"] for r in snap["rows"]] == [2490000, 2500000, 2510000]
    assert snap["rows"][0]["put"] is None  # no put listed at that strike


def test_pcr_max_pain_and_change_in_oi(world):
    service, md, price_all = world
    oi = {(k, K.CALL): 1000 for k in STRIKES} | {(k, K.PUT): 500 for k in STRIKES}
    oi[(25200, K.CALL)] = 20000  # heavy call writing above the market
    oi[(24800, K.PUT)] = 15000
    price_all(oi=oi)
    snap = service.snapshot("NIFTY", EXP, around=4)
    total_call = sum(v for (k, kind), v in oi.items() if kind is K.CALL)
    total_put = sum(v for (k, kind), v in oi.items() if kind is K.PUT)
    assert snap["pcr"] == pytest.approx(total_put / total_call, abs=1e-3)
    assert snap["max_pain_paise"] == max_pain(snap["rows"])
    assert 2480000 <= snap["max_pain_paise"] <= 2520000  # pinned between the two OI walls
    oi[(25200, K.CALL)] = 21500
    price_all(oi=oi)
    rows = service.snapshot("NIFTY", EXP, 4)["rows"]
    row = next(r for r in rows if r["strike_paise"] == 2520000)
    assert row["call"]["oi_change"] == 1500  # since the first OI seen today


def test_max_pain_by_hand():
    rows = [
        {"strike_paise": 100, "call": {"oi": 10}, "put": {"oi": 0}},
        {"strike_paise": 200, "call": {"oi": 0}, "put": {"oi": 0}},
        {"strike_paise": 300, "call": {"oi": 0}, "put": {"oi": 30}},
    ]
    # holders collect: at 100 -> puts 30*200 = 6000; at 200 -> calls 10*100 + puts 30*100 =
    # 4000; at 300 -> calls 10*200 = 2000. The least is at 300.
    assert max_pain(rows) == 300
    assert max_pain([]) is None


def test_a_watch_streams_atm_plus_minus_n_and_follows_the_atm(world):
    service, md, price_all = world
    price_all(forward=25010.0)
    watch = service.watch("NIFTY", EXP, around=1)
    expected = {FUT.id} | {
        option(k, kind).id for k in (24900, 25000, 25100) for kind in (K.CALL, K.PUT)
    }
    assert watch.subscription.instrument_ids == expected
    md.on_tick(Tick(FUT.id, 2521000))  # the market moved up two strikes
    watch.snapshot()
    assert option(25200, K.CALL).id in watch.subscription.instrument_ids
    assert option(24900, K.CALL).id not in watch.subscription.instrument_ids
    watch.close()
    assert md.watched() == set()
