"""Volume at price for the ladder (marketdata/vap.py): volume between ticks on the newer price."""

from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.vap import VolumeAtPrice

A = "NFO:NIFTY26OCTFUT"


def tick(ltp, volume, instrument_id=A):
    return Tick(instrument_id=instrument_id, ltp_paise=ltp, volume=volume)


def test_the_first_tick_is_only_a_baseline():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    assert vap.snapshot(A) == (1000, {})


def test_volume_between_ticks_goes_on_the_newer_last_price():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    vap.on_tick(tick(10005, 1150))
    vap.on_tick(tick(10000, 1225))
    vap.on_tick(tick(10005, 1300))
    assert vap.snapshot(A) == (1300, {10005: 225, 10000: 75})


def test_a_tick_without_new_volume_adds_nothing():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    vap.on_tick(tick(10010, 1000))  # the price moved on quotes alone
    assert vap.snapshot(A) == (1000, {})


def test_volume_going_backwards_starts_a_new_day():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    vap.on_tick(tick(10000, 1100))
    vap.on_tick(tick(10200, 300))  # next morning
    assert vap.snapshot(A) == (300, {})
    vap.on_tick(tick(10200, 375))
    assert vap.snapshot(A) == (375, {10200: 75})


def test_ticks_without_volume_are_ignored_and_instruments_kept_apart():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    vap.on_tick(Tick(instrument_id=A, ltp_paise=10050))  # light mode: no volume
    vap.on_tick(tick(10000, 1075))
    vap.on_tick(tick(500, 10, "NSE:SBIN"))
    assert vap.snapshot(A) == (1075, {10000: 75})
    assert vap.snapshot("NSE:SBIN") == (10, {})
    assert vap.snapshot("NSE:NOPE") == (None, {})


def test_snapshots_are_copies():
    vap = VolumeAtPrice()
    vap.on_tick(tick(10000, 1000))
    vap.on_tick(tick(10000, 1075))
    _, levels = vap.snapshot(A)
    levels[10000] = 0
    assert vap.snapshot(A) == (1075, {10000: 75})


def test_market_data_feeds_vap_on_every_tick():
    md = MarketData()
    md.on_tick(tick(10000, 1000))
    md.on_tick(tick(10005, 1075))
    assert md.vap.snapshot(A) == (1075, {10005: 75})
