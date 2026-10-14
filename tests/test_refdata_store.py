import pytest

from mocktrade.refdata.store import InstrumentStore


def test_mapping_requires_a_known_instrument():
    with pytest.raises(KeyError):
        InstrumentStore().map_broker("kite", "1", "NSE:NOPE")


def test_remapping_forgets_the_old_broker_ref(reliance):
    store = InstrumentStore()
    store.add(reliance)
    store.map_broker("kite", "111", reliance.id)
    store.map_broker("kite", "222", reliance.id)
    assert store.from_broker("kite", "111") is None
    assert store.from_broker("kite", "222") == reliance.id


def test_brokers_are_independent(reliance):
    store = InstrumentStore()
    store.add(reliance)
    store.map_broker("kite", "111", reliance.id)
    store.map_broker("upstox", "NSE_EQ|INE002A01018", reliance.id)
    assert store.to_broker("kite", reliance.id) == "111"
    assert store.to_broker("upstox", reliance.id) == "NSE_EQ|INE002A01018"


def test_search_is_prefix_case_insensitive_and_shortest_first(reliance, nifty_fut, nifty_index):
    store = InstrumentStore()
    for i in (reliance, nifty_fut, nifty_index):
        store.add(i)
    assert [i.symbol for i in store.search("nif")] == ["NIFTY 50", "NIFTY26OCTFUT"]
    assert store.search("   ") == []
