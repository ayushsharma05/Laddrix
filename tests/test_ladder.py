"""The text ladder for the terminal (marketdata/ladder.py)."""

from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.ladder import render_ladder


def test_ladder_shows_asks_above_bids_and_skips_empty_levels():
    tick = Tick(
        instrument_id="NFO:NIFTY26OCTFUT",
        ltp_paise=2501000,
        bids=(DepthLevel(2501000, 75, 1), DepthLevel(2500990, 150, 2), DepthLevel(0, 0, 0)),
        asks=(DepthLevel(2501010, 300, 3), DepthLevel(2501020, 75, 1)),
    )
    lines = render_ladder(tick).splitlines()
    assert lines[0].startswith("NFO:NIFTY26OCTFUT  LTP 25,010.00")
    prices = [line for line in lines if "|" in line][1:]
    assert "25,010.20" in prices[0] and "25,010.10" in prices[1]
    assert "25,010.00" in prices[2] and "25,009.90" in prices[3]
    assert len(prices) == 4
    assert tick.best_bid.qty == 75 and tick.best_ask.price_paise == 2501010
