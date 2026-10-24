"""Kite's tokens turned into our instrument ids (broker/kite/feed.py); unknown tokens counted,
never a crash.
"""

import pytest

from mocktrade.broker.kite.decoder import KiteTick
from mocktrade.broker.kite.feed import KiteFeed
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore


@pytest.fixture
def store(nifty_fut):
    s = InstrumentStore()
    s.add(nifty_fut)
    s.map_broker("kite", "13368834", nifty_fut.id)
    return s


def test_known_tokens_become_our_ticks(store, nifty_fut):
    md = MarketData()
    KiteFeed(store, md).on_ticks([KiteTick(token=13368834, mode="ltp", ltp_paise=2501000)])
    assert md.latest(nifty_fut.id).ltp_paise == 2501000


def test_unknown_tokens_are_counted_not_crashed(store):
    md = MarketData()
    feed = KiteFeed(store, md)
    feed.on_ticks([KiteTick(token=1, mode="ltp", ltp_paise=1)])
    assert feed.unknown_tokens == 1 and md.ticks_received == 0


def test_tokens_for_our_ids(store, nifty_fut):
    feed = KiteFeed(store, MarketData())
    assert feed.tokens_for([nifty_fut.id]) == [13368834]
    with pytest.raises(KeyError, match="NSE:NOPE"):
        feed.tokens_for(["NSE:NOPE"])
