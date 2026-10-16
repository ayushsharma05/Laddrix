"""The market simulator: Kite-shaped ticks, on the tick grid, repeatable."""

import asyncio
from datetime import date

import pytest

from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.simulator import MarketSimulator

FUT = Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, 10, 75, date(2026, 10, 27))
OPT = Instrument(Exchange.NFO, "NIFTY26OCT25000CE", InstrumentKind.CALL, 5, 75, date(2026, 10, 27))


def sim(seed=7, md=None):
    return MarketSimulator(md or MarketData(), {FUT: 2501000, OPT: 12005}, seed=seed)


def test_ticks_look_like_kite_full_mode():
    for tick in sim().step():
        assert len(tick.bids) == len(tick.asks) == 5
        assert tick.best_ask.price_paise - tick.best_bid.price_paise > 0
        assert tick.best_bid.price_paise <= tick.ltp_paise <= tick.best_ask.price_paise
        prices = [lvl.price_paise for lvl in tick.bids] + [lvl.price_paise for lvl in tick.asks]
        grid = 10 if tick.instrument_id == FUT.id else 5
        assert all(p % grid == 0 for p in prices + [tick.ltp_paise])
        assert tick.low_paise <= tick.ltp_paise <= tick.high_paise
        assert tick.last_qty % 75 == 0 and tick.volume == tick.last_qty


def test_volume_is_cumulative_and_published_to_market_data():
    md = MarketData()
    s = sim(md=md)
    first = s.step()[0].volume
    second = s.step()[0].volume
    assert second > first and md.latest(FUT.id).volume == second


def test_same_seed_same_market():
    a, b = sim(seed=3), sim(seed=3)
    for _ in range(50):
        assert [t.ltp_paise for t in a.step()] == [t.ltp_paise for t in b.step()]


def test_prices_stay_near_where_they_started():
    s = sim()
    for _ in range(3000):
        (fut, _) = s.step()
    assert abs(fut.ltp_paise - 2501000) < 2501000 * 0.05


def test_start_price_must_be_on_the_grid():
    with pytest.raises(ValueError, match="tick grid"):
        MarketSimulator(MarketData(), {FUT: 2501005})


async def test_run_publishes_until_cancelled():
    steps = []

    async def sleep(seconds):
        steps.append(seconds)
        if len(steps) == 3:
            raise asyncio.CancelledError

    s = MarketSimulator(MarketData(), {FUT: 2501000}, interval_s=1.0, sleep=sleep)
    with pytest.raises(asyncio.CancelledError):
        await s.run()
    assert steps == [1.0, 1.0, 1.0]


def test_options_start_their_day_at_their_model_price():
    """An option's previous close sits near the price it trades at (within 40 ticks), not near a
    placeholder start price, so its change from the close is believable (Movers, Watch)."""
    from mocktrade.marketdata.sim_universe import INSTRUMENTS

    md = MarketData()
    sim = MarketSimulator(md, {i: p for i, p in INSTRUMENTS}, seed=7)
    options = [i for i, _ in INSTRUMENTS if i.kind in (InstrumentKind.CALL, InstrumentKind.PUT)]
    assert options
    for t in sim.step():
        i = next((o for o in options if o.id == t.instrument_id), None)
        if i is not None:
            assert t.open_paise is not None and abs(t.open_paise - t.ltp_paise) <= 5 * i.tick_paise
            assert abs(t.ltp_paise - t.close_paise) <= 45 * i.tick_paise, (
                i.id,
                t.ltp_paise,
                t.close_paise,
            )
