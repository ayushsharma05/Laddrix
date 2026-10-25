"""Time & sales and movers (K12.4, #234), worked by hand.

The tape (NIFTY futures; book shown as bid / ask before each tick):

    tick 1  vol 1,000  ltp 25,000.00  book 24,999.90 / 25,000.10   first sight: baseline
    tick 2  vol 1,075  ltp 25,000.10  (before: 24,999.90 / 25,000.10)  75 at the ask: BUY
    tick 3  vol 1,225  ltp 24,999.90  (before: 25,000.00 / 25,000.20)  150 at the bid: SELL
    tick 4  vol 1,225  ltp 25,000.00                                   nothing traded
    tick 5  vol 1,300  ltp 25,000.05  (before: 24,999.90 / 25,000.10)  75 inside: unknown
"""

from datetime import UTC, datetime, timedelta

import pytest

from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.marketdata.movers import MAX_LIMIT, Movers, MoversError
from laddrix.marketdata.prints import TimeAndSales, print_to_dict

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


def book(ltp, volume, bid, ask, at=T0, instrument=FUT, **extra):
    side = {"bids": (DepthLevel(bid, 75, 1),), "asks": (DepthLevel(ask, 75, 1),)}
    return Tick(instrument, ltp, volume=volume, last_trade_time=at, **side, **extra)


def test_the_hand_worked_tape():
    tape = TimeAndSales()
    tape.on_tick(book(2500000, 1000, 2499990, 2500010))
    tape.on_tick(book(2500010, 1075, 2500000, 2500020, T0 + timedelta(seconds=1)))
    tape.on_tick(book(2499990, 1225, 2499990, 2500010, T0 + timedelta(seconds=2)))
    tape.on_tick(book(2500000, 1225, 2499990, 2500010, T0 + timedelta(seconds=3)))
    tape.on_tick(book(2500005, 1300, 2499990, 2500010, T0 + timedelta(seconds=4)))
    got = tape.recent(FUT)
    assert [(p.seq, p.price_paise, p.qty, p.side) for p in got] == [
        (1, 2500010, 75, "BUY"), (2, 2499990, 150, "SELL"), (3, 2500005, 75, "")]  # fmt: skip
    assert print_to_dict(got[0]) == {"seq": 1, "t": (T0 + timedelta(seconds=1)).isoformat(),
                                     "price_paise": 2500010, "qty": 75, "side": "BUY"}  # fmt: skip
    assert [p.seq for p in tape.recent(FUT, after=1)] == [2, 3]
    assert [p.seq for p in tape.recent(FUT, limit=1)] == [3]
    assert tape.recent("NSE:NOPE") == []


def test_exactly_at_the_previous_touch_counts_as_that_side():
    tape = TimeAndSales()
    tape.on_tick(book(2500000, 1000, 2499990, 2500010))
    tape.on_tick(book(2499990, 1050, 2499980, 2500000))  # at the old bid exactly: SELL
    tape.on_tick(book(2500000, 1100, 2499980, 2500000))  # at the old ask exactly: BUY
    assert [p.side for p in tape.recent(FUT)] == ["SELL", "BUY"]


def test_a_new_day_unwatched_contracts_and_the_size_kept():
    watched = {FUT}
    tape = TimeAndSales(keep=3, wanted=lambda i: i in watched)
    tape.on_tick(book(100, 1000, 99, 101))
    for n in range(1, 6):
        tape.on_tick(book(100 + n, 1000 + n, 99, 101))
    assert [p.seq for p in tape.recent(FUT)] == [3, 4, 5]  # only the newest 3 kept
    tape.on_tick(book(100, 10, 99, 101))  # the total fell: a new day, a baseline
    tape.on_tick(book(101, 20, 99, 101))
    assert tape.recent(FUT)[-1].qty == 10 and tape.recent(FUT)[-1].side == "BUY"
    watched.clear()
    tape.on_tick(book(102, 30, 99, 101))
    assert tape.recent(FUT) == []  # nobody watches: nothing kept
    tape.on_tick(Tick("NSE:NIFTY 50", 2500000))  # an index: no volume, no prints
    assert tape.recent("NSE:NIFTY 50") == []


def inst(symbol, exchange=Exchange.NSE, kind=InstrumentKind.EQUITY):
    return Instrument(exchange, symbol, kind, 5, 1)


@pytest.fixture
def market():
    store = {i.id: i for i in [inst("RELIANCE"), inst("INFY"), inst("SBIN"), inst("TCS"),
                               inst("NIFTY26OCTFUT", Exchange.NFO, InstrumentKind.FUTURE),
                               inst("NIFTY 50", kind=InstrumentKind.INDEX)]}  # fmt: skip
    ticks = {}
    now = [T0]
    movers = Movers(lambda: list(ticks.values()), store.get, clock=lambda: now[0])

    def tick(symbol, ltp, close=None, volume=None, oi=None, exchange="NSE"):
        t = Tick(f"{exchange}:{symbol}", ltp, close_paise=close, volume=volume, open_interest=oi)
        ticks[t.instrument_id] = t
        movers.on_tick(t)

    tick("RELIANCE", 102_00, 100_00, 5_000)  # +2%
    tick("INFY", 99_00, 100_00, 9_000)  # −1%
    tick("SBIN", 97_00, 100_00, 1_000)  # −3%
    tick("TCS", 100_00, 100_00, 7_000)  # unchanged
    tick("NIFTY 50", 2550000, 2500000)  # +2%, no volume
    tick("NIFTY26OCTFUT", 2500000, 2475000, 20_000, oi=1_000_000, exchange="NFO")  # +1.01%
    tick("NIFTY26OCTFUT", 2500000, 2475000, 21_000, oi=1_050_000, exchange="NFO")  # OI +5%
    ticks["NSE:GONE"] = Tick("NSE:GONE", 1)  # a price for something not in the list: ignored
    return movers, tick, now


def test_gainers_losers_volume_and_oi(market):
    movers, _, _ = market
    gain = movers.top("gainers")
    assert [(r["symbol"], r["change_pct"]) for r in gain["rows"]] == [
        ("NIFTY 50", 2.0),
        ("RELIANCE", 2.0),
        ("NIFTY26OCTFUT", 1.01),
    ]  # ties by id
    assert gain["of"] == 6
    assert gain["exchanges"] == ["NFO", "NSE"] and gain["kinds"] == ["EQ", "FUT", "INDEX"]
    assert [(r["symbol"], r["change_pct"]) for r in movers.top("losers")["rows"]] == [
        ("SBIN", -3.0), ("INFY", -1.0)]  # fmt: skip
    assert [r["symbol"] for r in movers.top("volume")["rows"]] == [
        "NIFTY26OCTFUT", "INFY", "TCS", "RELIANCE", "SBIN"]  # fmt: skip
    oi = movers.top("oi")
    assert [(r["symbol"], r["oi_change_pct"]) for r in oi["rows"]] == [("NIFTY26OCTFUT", 5.0)]
    assert oi["note"] == "OI change since the first open interest seen today"


def test_filters_limits_and_a_new_day_for_oi(market):
    movers, tick, now = market
    assert [r["symbol"] for r in movers.top("gainers", exchange="NSE", kind="EQ")["rows"]] == [
        "RELIANCE"]  # fmt: skip
    assert [r["symbol"] for r in movers.top("volume", limit=2)["rows"]] == ["NIFTY26OCTFUT", "INFY"]
    with pytest.raises(MoversError, match="by must be one of gainers, losers, volume, oi"):
        movers.top("value")
    for bad in (0, MAX_LIMIT + 1):
        with pytest.raises(MoversError, match="limit must be"):
            movers.top("gainers", limit=bad)
    now[0] += timedelta(days=1)
    tick(
        "NIFTY26OCTFUT", 2500000, 2500000, 100, oi=2_000_000, exchange="NFO"
    )  # a new day's baseline
    assert movers.top("oi")["rows"] == []  # no change yet today
    tick("NIFTY26OCTFUT", 2500000, 2500000, 200, oi=1_900_000, exchange="NFO")
    assert [r["oi_change_pct"] for r in movers.top("oi")["rows"]] == [-5.0]  # a fall counts too
    assert movers.top("oi", limit=5)["of"] == 6
    now[0] += timedelta(days=1)  # a day with no OI tick yet: yesterday's change isn't today's
    assert movers.top("oi")["rows"] == []


def test_exchange_alone_filters_and_oi_ranks_the_biggest_move_either_way(market):
    movers, tick, _ = market
    assert [r["symbol"] for r in movers.top("volume", exchange="NFO")["rows"]] == ["NIFTY26OCTFUT"]
    tick("RELIANCE", 102_00, 100_00, 5_000, oi=100_000)
    tick("RELIANCE", 102_00, 100_00, 5_100, oi=92_000)  # OI −8%: a bigger move than the +5%
    assert [(r["symbol"], r["oi_change_pct"]) for r in movers.top("oi")["rows"]] == [
        ("RELIANCE", -8.0), ("NIFTY26OCTFUT", 5.0)]  # fmt: skip


async def test_the_api(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)
    c = await w.logged_in()
    md = w.platform.market_data
    watch = md.subscribe([FUT])  # a screen watches it: prints are kept
    for t in (book(2500000, 1000, 2499990, 2500010), book(2500010, 1075, 2500000, 2500020)):
        md.on_tick(t)
    got = (await c.get(f"/api/instruments/{FUT}/prints")).json()
    assert [(p["seq"], p["qty"], p["side"]) for p in got["prints"]] == [(1, 75, "BUY")]
    assert got["watched"] is True and "estimated" in got["estimate"]
    assert (await c.get(f"/api/instruments/{FUT}/prints", params={"after": 1})).json()[
        "prints"
    ] == []
    assert (await c.get("/api/instruments/NFO:NOPE/prints")).status_code == 404
    movers = (await c.get("/api/market/movers", params={"by": "volume"})).json()
    assert movers["rows"][0]["instrument_id"] == FUT and movers["rows"][0]["volume"] == 1075
    bad = await c.get("/api/market/movers", params={"by": "value"})
    assert bad.status_code == 422 and "by must be one of" in bad.json()["detail"]
    watch.close()
    await c.aclose()
    w.log.close()
