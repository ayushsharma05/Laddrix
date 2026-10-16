"""Recordings keep the day's instrument list (V1.1, #327): each recorded instrument's details are
saved with the day before its first ticks, read back exactly, and a replay trades on them with
no live instrument list, even for a contract that has since expired. A day that can't be
described is refused before the replay starts, naming what is missing."""

import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_ticks_recorder import full

from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sim_universe import INSTRUMENTS as DEMO
from mocktrade.refdata.store import InstrumentStore, instrument_from_record, instrument_record
from mocktrade.research.replay import (
    ReplayClock,
    ReplayFeed,
    ReplayInstrumentsError,
    replay_instruments,
)
from mocktrade.research.ticks import INSTRUMENTS, FileTickStore, NoInstrumentList

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2025, 1, 20, 9, 15, tzinfo=IST)
DAY = T0.date()
# a real contract that has expired since (not one of the demo's): its details exist only in
# the recording once the broker's list has moved on
BANK = Instrument(Exchange.NFO, "BANKNIFTY25JANFUT", InstrumentKind.FUTURE, tick_paise=20,
                  lot_size=30, expiry=date(2025, 1, 30), underlying="BANKNIFTY")  # fmt: skip
CALL = Instrument(Exchange.NFO, "NIFTY25JAN23500CE", InstrumentKind.CALL, tick_paise=5,
                  lot_size=75, expiry=date(2025, 1, 30), strike_paise=2_350_000,
                  underlying="NIFTY")  # fmt: skip
INDEX = Instrument(Exchange.NSE, "NIFTY 50", InstrumentKind.INDEX, tick_paise=5, lot_size=1)
SHARE = Instrument(Exchange.NSE, "RELIANCE", InstrumentKind.EQUITY, tick_paise=10, lot_size=1)
MASTER = {i.id: i for i in (BANK, CALL, INDEX, SHARE)}


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def ticks_of(*ids, start=0):
    return [(full(i, 4_900_000, at(start + n)), at(start + n)) for n, i in enumerate(ids)]


# --- one instrument as JSON ---------------------------------------------------------------------


def test_every_kind_of_instrument_round_trips():
    for instrument in [*MASTER.values(), *(i for i, _ in DEMO)]:
        record = instrument_record(instrument)
        assert json.loads(json.dumps(record)) == record  # plain JSON
        assert instrument_from_record(record) == instrument


@pytest.mark.parametrize(
    "change, says",
    [
        ({"lot_size": None}, "not valid"),
        ({"kind": "SWAP"}, "SWAP"),
        ({"exchange": "LSE"}, "LSE"),
        ({"expiry": "next week"}, "next week"),
        ({"tick_paise": 0}, "tick must be positive"),
        ({"strike_paise": "lots"}, "lots"),
    ],
)
def test_a_broken_record_is_refused_saying_why(change, says):
    with pytest.raises(ValueError, match=says):
        instrument_from_record({**instrument_record(BANK), **change})
    record = instrument_record(BANK)
    del record["symbol"]
    with pytest.raises(ValueError, match="without 'symbol'"):
        instrument_from_record(record)


# --- the recording keeps the list ---------------------------------------------------------------


def test_each_instrument_is_described_once_a_day_before_its_first_ticks(tmp_path):
    asked = []

    def describe(instrument_id):
        # none of its ticks are on disk yet: a crash in between leaves no undescribed tick
        on_disk = any(t.instrument_id == instrument_id for t in store.read(DAY))
        asked.append((instrument_id, on_disk))
        return MASTER.get(instrument_id)

    store = FileTickStore(tmp_path, describe=describe)
    store.append(ticks_of(BANK.id, SHARE.id, BANK.id))
    store.append(ticks_of(BANK.id, CALL.id, start=10))
    assert asked == [(BANK.id, False), (SHARE.id, False), (CALL.id, False)]
    by_id = sorted(store.instruments(DAY), key=lambda i: i.id)
    assert by_id == sorted([BANK, SHARE, CALL], key=lambda i: i.id)
    # the next India day starts its own list
    store.append(ticks_of(SHARE.id, start=86_400))
    assert store.instruments(DAY + timedelta(days=1)) == [SHARE]


def test_a_restarted_recorder_adds_to_the_day_s_list(tmp_path):
    FileTickStore(tmp_path, describe=MASTER.get).append(ticks_of(BANK.id))
    asked = []
    again = FileTickStore(tmp_path, describe=lambda i: asked.append(i) or MASTER.get(i))
    again.append(ticks_of(BANK.id, CALL.id, start=60))
    assert asked == [CALL.id]  # BANK was already in the list on disk
    assert {i.id for i in again.instruments(DAY)} == {BANK.id, CALL.id}


def test_an_instrument_the_master_does_not_know_is_left_out_and_asked_again(tmp_path):
    known = {}
    store = FileTickStore(tmp_path, describe=known.get)
    store.append(ticks_of(BANK.id))
    with pytest.raises(NoInstrumentList):  # nothing it could say: no list yet
        store.instruments(DAY)
    known[SHARE.id] = SHARE
    store.append(ticks_of(BANK.id, SHARE.id, start=5))  # one it knows, one it doesn't
    assert store.instruments(DAY) == [SHARE]
    known[BANK.id] = BANK  # the master learns it (the morning refresh)
    store.append(ticks_of(BANK.id, start=10))
    assert {i.id for i in store.instruments(DAY)} == {BANK.id, SHARE.id}


def test_without_a_master_no_list_is_kept(tmp_path):
    store = FileTickStore(tmp_path)
    store.append(ticks_of(BANK.id))
    with pytest.raises(NoInstrumentList, match="2025-01-20: no instrument list"):
        store.instruments(DAY)


def test_a_broken_list_names_the_day_and_is_rewritten_whole_by_the_recorder(tmp_path):
    store = FileTickStore(tmp_path, describe=MASTER.get)
    store.append(ticks_of(BANK.id))
    (tmp_path / DAY.isoformat() / INSTRUMENTS).write_text("{not json")
    with pytest.raises(ValueError, match="2025-01-20: the instrument list .* is broken"):
        store.instruments(DAY)
    fresh = FileTickStore(tmp_path, describe=MASTER.get)  # a restart: the broken file is replaced
    fresh.append(ticks_of(CALL.id, start=5))
    assert fresh.instruments(DAY) == [CALL]
    (tmp_path / DAY.isoformat() / INSTRUMENTS).write_text("[1, 2]")  # JSON, but not a list of them
    again = FileTickStore(tmp_path, describe=MASTER.get)
    again.append(ticks_of(BANK.id, start=9))
    assert again.instruments(DAY) == [BANK]


# --- a replay uses it ---------------------------------------------------------------------------


def test_a_replay_takes_the_day_s_own_instruments(tmp_path):
    store = FileTickStore(tmp_path, describe=MASTER.get)
    store.append(ticks_of(BANK.id, CALL.id))
    assert replay_instruments(store, DAY) == [BANK, CALL]  # in id order
    # a different lot in the demo's (today's) list doesn't matter: the day's own is used
    later = Instrument(Exchange.NFO, BANK.symbol, BANK.kind, 20, 15, BANK.expiry, None, "BANKNIFTY")
    assert replay_instruments(store, DAY, [later])[0].lot_size == 30


def test_an_old_demo_day_without_a_list_still_replays(tmp_path):
    demo = [i for i, _ in DEMO]
    store = FileTickStore(tmp_path)  # recorded before lists were kept
    store.append(ticks_of(demo[0].id, demo[1].id))
    assert replay_instruments(store, DAY, demo) == sorted(demo[:2], key=lambda i: i.id)


def test_a_day_that_can_t_be_described_is_refused_naming_what_is_missing(tmp_path):
    store = FileTickStore(tmp_path)
    many = [f"NFO:X{n}FUT" for n in range(7)]
    store.append(ticks_of(BANK.id, *many))
    with pytest.raises(ReplayInstrumentsError) as caught:
        replay_instruments(store, DAY, [i for i, _ in DEMO])
    text = str(caught.value)
    assert text.startswith(
        "2025-01-20: 8 recorded instrument(s) have no details (lot, tick, expiry)"
    )
    assert "NFO:BANKNIFTY25JANFUT, NFO:X0FUT, NFO:X1FUT, NFO:X2FUT, NFO:X3FUT and 3 more" in text
    assert "days recorded from now on keep it" in text
    (tmp_path / DAY.isoformat() / INSTRUMENTS).write_text("[1, 2]")  # broken, not just missing
    with pytest.raises(ReplayInstrumentsError, match="is broken.*Delete or fix that file"):
        replay_instruments(store, DAY)


async def test_the_demo_refuses_such_a_day_before_starting(tmp_path, monkeypatch):
    from mocktrade.tools import demo

    FileTickStore(tmp_path / "ticks").append(ticks_of(BANK.id))
    monkeypatch.setattr(demo, "REPLAYS", tmp_path / "replay")
    with pytest.raises(SystemExit, match="can't replay: 2025-01-20: 1 recorded instrument"):
        await demo.main(8999, "http://localhost:8999", None, replay_day=DAY,
                        ticks_dir=tmp_path / "ticks")  # fmt: skip
    assert not (tmp_path / "replay").exists()  # refused before anything was created


async def test_an_expired_contract_s_day_replays_and_trades_on_paper_with_no_live_list(tmp_path):
    def book(ltp):
        return Tick(BANK.id, ltp, bids=(DepthLevel(ltp - 20, 300, 2),),
                    asks=(DepthLevel(ltp + 20, 300, 2),))  # fmt: skip

    recorded = FileTickStore(tmp_path, describe=MASTER.get)  # the live server that day
    prices = [4_900_000, 4_899_980, 4_899_960, 4_899_940]
    recorded.append([(book(p), at(n)) for n, p in enumerate(prices)])

    # months later, a replay server: only the recording, no broker, no instrument master
    instruments = InstrumentStore()
    for instrument in replay_instruments(FileTickStore(tmp_path), DAY, [i for i, _ in DEMO]):
        instruments.add(instrument)
    md = MarketData()
    broker = PaperBroker(md, instruments, latency_s=0.0, every_tick=True)
    broker.start()
    placed = []

    async def buy_on_the_second_tick(tick):
        if len(placed) == 1:
            await broker.place(OrderRequest("MT1", "acc", "a", BANK.id, Side.BUY, 60,
                                            OrderType.LIMIT, Product.NRML,
                                            price_paise=4_899_960))  # fmt: skip
        placed.append(tick)

    pending = []
    md.tap(lambda t: pending.append(asyncio.ensure_future(buy_on_the_second_tick(t))))
    feed = ReplayFeed(lambda: recorded.read_timed(DAY), md, ReplayClock(T0), day=DAY, speed=0)
    await feed.run()
    await asyncio.gather(*pending)
    await broker.settle()
    fills = [(t.qty, t.price_paise) for t in await broker.trades()]
    await broker.aclose()
    assert sum(q for q, _ in fills) == 60  # two lots of 30: the day's lot size, filled
    with pytest.raises(ValueError, match="multiple of BANKNIFTY25JANFUT's lot size 30"):
        instruments.get(BANK.id).check_quantity(45)
