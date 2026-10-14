import asyncio

from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData


def tick(instrument_id, ltp):
    return Tick(instrument_id=instrument_id, ltp_paise=ltp)


def test_latest_keeps_only_the_newest():
    md = MarketData()
    md.on_tick(tick("NSE:A", 1))
    md.on_tick(tick("NSE:A", 2))
    assert md.latest("NSE:A").ltp_paise == 2
    assert md.latest("NSE:B") is None
    assert md.ticks_received == 2


def test_slow_subscriber_gets_only_the_newest_tick_per_instrument():
    md = MarketData()
    sub = md.subscribe(["NSE:A", "NSE:B"])
    for ltp in (1, 2, 3):
        md.on_tick(tick("NSE:A", ltp))
    md.on_tick(tick("NSE:B", 9))
    batch = {t.instrument_id: t.ltp_paise for t in sub.drain()}
    assert batch == {"NSE:A": 3, "NSE:B": 9}
    assert sub.drain() == []


def test_unwatched_instruments_are_not_delivered():
    md = MarketData()
    sub = md.subscribe(["NSE:A"])
    md.on_tick(tick("NSE:B", 1))
    assert sub.drain() == []


def test_adding_an_instrument_delivers_its_current_state_immediately():
    md = MarketData()
    md.on_tick(tick("NSE:A", 5))
    sub = md.subscribe([])
    sub.add(["NSE:A"])
    assert [t.ltp_paise for t in sub.drain()] == [5]


def test_remove_and_close_stop_delivery_and_update_watched():
    md = MarketData()
    a, b = md.subscribe(["NSE:A"]), md.subscribe(["NSE:A", "NSE:B"])
    b.remove(["NSE:B"])
    assert md.watched() == {"NSE:A"}
    a.close()
    b.close()
    md.on_tick(tick("NSE:A", 1))
    assert md.watched() == set()
    assert a.drain() == [] and b.drain() == []


async def test_next_batch_waits_for_a_change():
    md = MarketData()
    sub = md.subscribe(["NSE:A"])
    waiter = asyncio.create_task(sub.next_batch())
    await asyncio.sleep(0)
    assert not waiter.done()
    md.on_tick(tick("NSE:A", 7))
    assert [t.ltp_paise for t in await asyncio.wait_for(waiter, 1)] == [7]


async def test_closing_wakes_a_waiting_reader():
    md = MarketData()
    sub = md.subscribe(["NSE:A"])
    waiter = asyncio.create_task(sub.next_batch())
    await asyncio.sleep(0)
    sub.close()
    assert await asyncio.wait_for(waiter, 1) == []
