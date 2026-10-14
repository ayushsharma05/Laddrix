from datetime import date
from pathlib import Path

from mocktrade.broker.kite.instruments import load_kite_instruments
from mocktrade.domain.instruments import InstrumentKind
from mocktrade.refdata.store import InstrumentStore

SAMPLE = (Path(__file__).parent / "data" / "kite_instruments_sample.csv").read_text()


def test_sample_dump_loads_with_a_report():
    store = InstrumentStore()
    report = load_kite_instruments(SAMPLE, store)
    assert (report.loaded, report.skipped, report.bad) == (5, 2, 1)
    assert len(store) == 5


def test_fields_are_converted_to_paise_and_our_ids():
    store = InstrumentStore()
    load_kite_instruments(SAMPLE, store)
    option = store.get("NFO:NIFTY26OCT25000CE")
    assert option.kind is InstrumentKind.CALL
    assert (option.tick_paise, option.lot_size, option.strike_paise) == (5, 75, 2500000)
    assert option.expiry == date(2026, 10, 27)
    assert option.underlying == "NIFTY"


def test_index_gets_safe_tick_and_lot():
    store = InstrumentStore()
    load_kite_instruments(SAMPLE, store)
    index = store.get("NSE:NIFTY 50")
    assert index.kind is InstrumentKind.INDEX and not index.tradable
    assert (index.tick_paise, index.lot_size) == (1, 1)


def test_kite_tokens_map_both_ways_and_same_symbol_on_two_exchanges_stays_apart():
    store = InstrumentStore()
    load_kite_instruments(SAMPLE, store)
    assert store.from_broker("kite", "738561") == "NSE:RELIANCE"
    assert store.from_broker("kite", "128083204") == "BSE:RELIANCE"
    assert store.to_broker("kite", "NFO:NIFTY26OCTFUT") == "13368834"


def test_reloading_replaces_without_duplicates():
    store = InstrumentStore()
    load_kite_instruments(SAMPLE, store)
    load_kite_instruments(SAMPLE, store)
    assert len(store) == 5
