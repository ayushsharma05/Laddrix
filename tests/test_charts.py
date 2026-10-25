"""Candles and charts (K12.1, #231): ticks into OHLCV by hand, buckets on the session, history
joined to live with each bucket once, Kite history split, spaced and cached, recordings as
history, and the account's fills as marks.

The hand-worked minute (NIFTY futures, the day's total volume in brackets):

    09:15:10  100.00 (1,000)   first sight: a baseline, nothing counted
    09:15:20  100.05 (1,150)   +150
    09:15:30  100.05 (1,150)   a quote: no trade, ignored
    09:15:40   99.90 (1,200)   +50
    09:16:05  100.10 (1,300)   +100, a new minute
    → 09:15  O 100.00  H 100.05  L 99.90  C 99.90  V 200;   09:16  O=H=L=C 100.10  V 100
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from laddrix.charts.candles import (
    BY_CODE,
    MINUTE,
    Candle,
    CandleBuilder,
    Interval,
    aggregate,
    bucket_start,
)
from laddrix.charts.history import HistoryError, KiteHistory, RecordedHistory
from laddrix.charts.service import ChartError, Charts
from laddrix.domain.ticks import Tick

IST = ZoneInfo("Asia/Kolkata")
FUT = "NFO:NIFTY26OCTFUT"
DAY = date(2026, 10, 16)


def t(hh, mm=0, ss=0, day=DAY):
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST)


def tick(at, ltp, volume=None, oi=None, instrument=FUT):
    return Tick(instrument, ltp, exchange_time=at, volume=volume, open_interest=oi)


def ohlcv(c):
    return (c.start.strftime("%H:%M"), c.open, c.high, c.low, c.close, c.volume)


def hand_worked(builder=None):
    b = builder or CandleBuilder()
    for at, ltp, vol in [(t(9, 15, 10), 10000, 1000), (t(9, 15, 20), 10005, 1150),
                         (t(9, 15, 30), 10005, 1150), (t(9, 15, 40), 9990, 1200),
                         (t(9, 16, 5), 10010, 1300)]:  # fmt: skip
        b.on_tick(tick(at, ltp, vol))
    return b


def test_the_hand_worked_minute():
    b = hand_worked()
    got = b.candles(FUT, MINUTE, t(9, 0), t(10, 0))
    assert [ohlcv(c) for c in got] == [("09:15", 10000, 10005, 9990, 9990, 200),
                                       ("09:16", 10010, 10010, 10010, 10010, 100)]  # fmt: skip
    assert b.complete_from(FUT) == t(9, 16)  # the first minute was seen part-way
    assert b.forming(FUT, MINUTE).start == t(9, 16)
    assert b.complete_from("NSE:NOPE") is None and b.candles("NSE:NOPE", MINUTE, t(9), t(10)) == []
    assert b.forming("NSE:NOPE", MINUTE) is None


def test_a_quote_in_a_new_minute_opens_no_candle():
    b = hand_worked()
    b.on_tick(tick(t(9, 17, 1), 10010, 1300))  # no new volume, the same price: a quote
    b.on_tick(tick(t(9, 18, 1), 10020, 1300))  # a new price without volume still draws
    got = b.candles(FUT, MINUTE, t(9), t(10))
    assert [c.start for c in got] == [t(9, 15), t(9, 16), t(9, 18)]


@pytest.mark.parametrize(
    "at, code, start",
    [
        (t(9, 29, 59), "15m", t(9, 15)),
        (t(9, 30), "15m", t(9, 30)),
        (t(10, 14, 59), "1h", t(9, 15)),
        (t(10, 15), "1h", t(10, 15)),
        (t(15, 29), "1h", t(15, 15)),
        (t(9, 7), "1h", t(8, 15)),  # pre-open: still on the session's grid
        (t(9, 16, 30), "3m", t(9, 15)),
        (t(9, 18), "3m", t(9, 18)),
        (t(14, 0), "1d", t(0, 0)),
        (t(0, 10).astimezone(UTC), "1d", t(0, 0)),  # a UTC time is placed on the India day
    ],
)
def test_buckets_follow_the_session_in_india_time(at, code, start):
    assert bucket_start(at, BY_CODE[code], "NFO") == start


def test_a_new_day_volume_open_interest_and_late_ticks():
    b = CandleBuilder()
    b.on_tick(tick(t(15, 29, 50), 10000, 9_000, oi=500))
    b.on_tick(tick(t(15, 29, 55), 10010, 9_075, oi=575))
    nxt = DAY + timedelta(days=1)
    b.on_tick(tick(t(9, 15, 1, nxt), 10100, 300, oi=600))  # the total fell: a new day, a baseline
    b.on_tick(tick(t(9, 15, 2, nxt), 10110, 375, oi=640))
    b.on_tick(tick(t(9, 14, 59, nxt), 1, 400))  # late: its minute is closed
    assert b.out_of_order == 1
    rows = b.candles(FUT, MINUTE, t(0, 0), t(23, 0, 0, nxt))
    assert [(ohlcv(c), c.oi) for c in rows] == [
        (("15:29", 10000, 10010, 10000, 10010, 75), 575),
        (("09:15", 10100, 10110, 10100, 10110, 75), 640),
    ]
    day = b.candles(FUT, BY_CODE["1d"], t(0, 0), t(0, 0, 0, nxt + timedelta(days=1)))
    assert [(c.start.date(), c.volume) for c in day] == [(DAY, 75), (nxt, 75)]


def test_an_index_without_volume_still_draws():
    b = CandleBuilder()
    b.on_tick(tick(t(9, 15, 1), 2500000, instrument="NSE:NIFTY 50"))
    b.on_tick(tick(t(9, 15, 2), 2500000, instrument="NSE:NIFTY 50"))
    b.on_tick(tick(t(9, 16, 2), 2500100, instrument="NSE:NIFTY 50"))
    got = b.candles("NSE:NIFTY 50", MINUTE, t(9), t(10))
    assert [ohlcv(c) for c in got] == [
        ("09:15", 2500000, 2500000, 2500000, 2500000, 0),
        ("09:16", 2500100, 2500100, 2500100, 2500100, 0),
    ]


def test_minutes_add_up_into_bigger_candles_and_the_forming_one():
    b = CandleBuilder()
    vol = 1000
    b.on_tick(tick(t(9, 15), 100, vol))
    for n, price in enumerate([101, 99, 104, 102, 103, 98, 100]):  # one a minute from 09:15
        vol += 10 * (n + 1)
        b.on_tick(tick(t(9, 15 + n, 30), price, vol, oi=n))
    five = b.candles(FUT, BY_CODE["5m"], t(9, 15), t(9, 30))
    assert [(ohlcv(c), c.oi) for c in five] == [
        (("09:15", 100, 104, 99, 103, 150), 4),
        (("09:20", 98, 100, 98, 100, 130), 6),
    ]
    assert ohlcv(b.forming(FUT, BY_CODE["5m"])) == ("09:20", 98, 100, 98, 100, 130)
    assert ohlcv(b.forming(FUT, BY_CODE["1h"])) == ("09:15", 100, 104, 98, 100, 280)
    assert b.candles(FUT, BY_CODE["5m"], t(9, 20), t(9, 30))[0].start == t(9, 20)


def test_only_the_newest_minutes_are_kept():
    b = CandleBuilder(keep_minutes=3)
    for m in range(5):
        b.on_tick(tick(t(9, 15 + m), 100 + m))
    assert [c.start for c in b.candles(FUT, MINUTE, t(9), t(10))] == [t(9, 17), t(9, 18), t(9, 19)]


# --- history joined to live -----------------------------------------------------------------


class FakeHistory:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.asked = []

    async def fetch(self, instrument_id, interval, start, end):
        self.asked.append((instrument_id, interval.code, start, end))
        if self.error:
            raise HistoryError(self.error)
        mine = [r for r in self.rows if start <= r.start < end]
        return aggregate(mine, interval, "NFO") if interval.code != "1m" else mine


def c(hh, mm, o, v=10):
    return Candle(t(hh, mm), o, o + 5, o - 5, o + 1, v)


async def test_each_bucket_once_history_before_the_builder_saw_it_whole_live_after():
    # the broker's 1-minute history up to 09:18 (its 09:18 is partial: fetched mid-minute)
    history = FakeHistory([c(9, 15, 100), c(9, 16, 110), c(9, 17, 120), c(9, 18, 130, v=1)])
    b = CandleBuilder()
    b.on_tick(tick(t(9, 17, 30), 999, 5000))  # the builder starts part-way through 09:17
    b.on_tick(tick(t(9, 17, 40), 998, 5010))
    b.on_tick(tick(t(9, 18, 10), 140, 5050))
    b.on_tick(tick(t(9, 19, 10), 150, 5070))
    charts = Charts(b, history)
    got = await charts.candles(FUT, "1m", t(9, 15), t(9, 30))
    assert [(r["t"][11:16], r["o"], r["v"]) for r in got["candles"]] == [
        ("09:15", 100, 10),
        ("09:16", 110, 10),
        ("09:17", 120, 10),  # history (09:17 whole there)
        ("09:18", 140, 40),
        ("09:19", 150, 20),  # live: seen whole, fresher than the broker's
    ]
    assert got["live_from"] == t(9, 18).isoformat() and got["note"] is None
    five = await charts.candles(FUT, "5m", t(9, 15), t(9, 30))
    assert [(r["t"][11:16], r["o"], r["v"]) for r in five["candles"]] == [("09:15", 100, 31)]
    assert five["live_from"] == t(9, 20).isoformat()  # the live 09:15 bucket is partial


async def test_without_history_or_with_it_failing_the_live_candles_still_come():
    b = hand_worked()
    alone = await Charts(b, None).candles(FUT, "1m", t(9), t(10))
    assert len(alone["candles"]) == 2 and alone["note"] is None
    broken = await Charts(b, FakeHistory(error="Kite history: TokenException")).candles(
        FUT, "1m", t(9), t(10)
    )
    assert len(broken["candles"]) == 2 and broken["note"] == "Kite history: TokenException"


async def test_requests_are_checked():
    charts = Charts(CandleBuilder(), None)
    with pytest.raises(ChartError, match="interval must be one of 1m, 3m, 5m"):
        await charts.candles(FUT, "2m", t(9), t(10))
    with pytest.raises(ChartError, match="the range must end after it starts"):
        await charts.candles(FUT, "1m", t(10), t(10))
    with pytest.raises(ChartError, match="at most 5 days of 1 minute candles"):
        await charts.candles(FUT, "1m", t(9), t(9) + timedelta(days=6))
    assert (await charts.candles(FUT, "1m", t(9), t(9) + timedelta(days=5)))["candles"] == []
    spec = charts.spec()
    assert [i["code"] for i in spec["intervals"]] == ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
    assert spec["intervals"][0] == {"code": "1m", "label": "1 minute", "seconds": 60,
                                    "max_range_days": 5, "default_days": 1}  # fmt: skip
    assert spec["history"] is None


# --- Kite history ---------------------------------------------------------------------------


class FakeKite:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def request(self, method, path, *, params=None, **_):
        self.calls.append((method, path, params["from"], params["to"]))
        if self.fail:
            raise self.fail
        day = date.fromisoformat(params["from"][:10])
        return {"candles": [
            [f"{day.isoformat()}T09:15:00+0530", Decimal("25010.5"), Decimal("25020"),
             Decimal("25000.05"), Decimal("25015"), 7500, 120000],
            [f"{day.isoformat()}T09:16:00+0530", Decimal("25015"), Decimal("25016"),
             Decimal("25014"), Decimal("25016"), 75],  # no OI column
        ]}  # fmt: skip


async def test_kite_history_is_split_spaced_cached_and_read_exactly():
    kite = FakeKite()
    waits = []
    now = [100.0]

    async def sleep(s):
        waits.append(round(s, 3))
        now[0] += s

    hist = KiteHistory(kite, {FUT: "12345"}.get, per_second=4, clock=lambda: t(12, 0),
                       sleep=sleep, monotonic=lambda: now[0])  # fmt: skip
    two = Interval("1m", "1 minute", 60, "minute", 2, 5, 1)  # Kite allows 2 days a request here
    first = DAY - timedelta(days=4)
    rows = await hist.fetch(FUT, two, t(0, 0, 0, first), t(0, 0, 0, DAY + timedelta(days=1)))
    assert [(c[2][:10], c[3][:10]) for c in kite.calls] == [
        ("2026-10-12", "2026-10-13"),
        ("2026-10-14", "2026-10-15"),
        ("2026-10-16", "2026-10-16"),
    ]
    assert kite.calls[0][:2] == ("GET", "/instruments/historical/12345/minute")
    assert waits == [0.25, 0.25]  # 4 a second: spaced, never bunched
    assert len(rows) == 6  # the first candle of each window's first day (fake), and the next
    r = rows[0]
    assert (r.start, r.open, r.high, r.low, r.close, r.volume, r.oi) == (
        t(9, 15, 0, first),
        2501050,
        2502000,
        2500005,
        2501500,
        7500,
        120000,
    )
    assert rows[1].oi is None
    await hist.fetch(FUT, two, t(0, 0, 0, first), t(0, 0, 0, DAY + timedelta(days=1)))
    assert len(kite.calls) == 4  # the finished windows from memory; today's asked again
    with pytest.raises(HistoryError, match="Kite has no history for NSE:NOPE"):
        await hist.fetch("NSE:NOPE", two, t(9), t(10))
    broken = KiteHistory(FakeKite(fail=RuntimeError("down")), {FUT: "1"}.get, sleep=sleep)
    with pytest.raises(HistoryError, match="Kite history: down"):
        await broken.fetch(FUT, two, t(9), t(10))


async def test_recorded_ticks_make_the_history_on_paper(tmp_path):
    from laddrix.research.ticks import FileTickStore

    store = FileTickStore(tmp_path)
    y = DAY - timedelta(days=1)
    rows = [(tick(t(9, 15, 10, y), 100, 1000), t(9, 15, 10, y)),
            (tick(t(9, 15, 20, y), 105, 1100), t(9, 15, 20, y)),
            (Tick(FUT, 103, volume=1150), t(9, 16, 5, y)),  # no time on the tick: when recorded
            (tick(t(9, 15, 5), 200, 50), t(9, 15, 5))]  # fmt: skip
    store.append(rows)
    mono = [0.0]
    hist = RecordedHistory(store, clock=lambda: t(12, 0), monotonic=lambda: mono[0])
    got = await hist.fetch(FUT, MINUTE, t(9, 0, 0, y), t(10, 0))
    assert [(c.start, c.open, c.close, c.volume) for c in got] == [
        (t(9, 15, 0, y), 100, 105, 100),
        (t(9, 16, 0, y), 103, 103, 50),
        (t(9, 15), 200, 200, 0),
    ]
    store.append([(tick(t(9, 15, 30), 210, 80), t(9, 15, 30))])  # today grows; yesterday is kept
    assert hist.builds == 2
    soon = await hist.fetch(FUT, BY_CODE["1d"], t(0, 0), t(0, 0, 0, DAY + timedelta(days=1)))
    assert [(c.close, c.volume) for c in soon] == [(200, 0)] and hist.builds == 2  # just built
    mono[0] = 301.0  # five minutes on
    again = await hist.fetch(FUT, BY_CODE["1d"], t(0, 0, 0, y), t(0, 0, 0, DAY + timedelta(days=1)))
    assert [(c.start.date(), c.close, c.volume) for c in again] == [(y, 103, 150), (DAY, 210, 30)]
    assert hist.builds == 3  # today again; yesterday from memory
    mono[0] = 700.0
    both = await asyncio.gather(*(hist.fetch(FUT, MINUTE, t(9), t(10)) for _ in range(3)))
    assert hist.builds == 4 and both[0] == both[1] == both[2]  # three requests, one build


# --- marks ------------------------------------------------------------------------------------


async def test_the_accounts_fills_become_marks_each_once(tmp_path):
    from test_reports import fill

    from laddrix.domain.orders import Side
    from laddrix.store.events import EventLog

    log = EventLog(tmp_path / "events.db")
    log.start()
    fill(log, "M1", Side.BUY, 75, 25000, t(10, 0).astimezone(UTC))
    fill(log, "M1", Side.BUY, 75, 25000, t(10, 1).astimezone(UTC))  # the same fill read twice
    fill(log, "M2", Side.SELL, 150, 25100, t(11, 0).astimezone(UTC), basket="B1",
         fills=[(75, 2510000), (75, 2510500)])  # fmt: skip
    fill(log, "M3", Side.BUY, 75, 25200, t(9, 0, 0, DAY - timedelta(days=1)).astimezone(UTC))
    await log.flush()
    charts = Charts(CandleBuilder(), None, events=log)
    marks = charts.marks("acc", FUT, t(9, 15), t(15, 30))
    assert [
        (m["t"][11:16], m["side"], m["qty"], m["price_paise"], m["strategy"]) for m in marks
    ] == [
        ("04:30", "BUY", 75, 2500000, "manual"),
        ("05:30", "SELL", 75, 2510000, "basket B1"),
        ("05:30", "SELL", 75, 2510500, "basket B1"),
    ]
    assert charts.marks("acc", "NFO:OTHER", t(9), t(16)) == []
    assert Charts(CandleBuilder(), None).marks("acc", FUT, t(9), t(16)) == []
    await log.aclose()


# --- the API and the stream ---------------------------------------------------------------------


@pytest.fixture
async def live(tmp_path, nifty_fut):
    """A running server with charts: the builder taps every tick, history from a fake."""
    from test_stream import Live

    from laddrix.server.app import ServerSettings, create_app

    server = Live(tmp_path, nifty_fut)
    builder = CandleBuilder()
    server.md.tap(builder.on_tick)
    history = FakeHistory([c(9, 15, 2500000)])
    server.charts = Charts(builder, history, events=None, history_name="test history")
    server.app = create_app(server.platform, server.users,
                            ServerSettings(origin=server.origin, cookie_secure=False),
                            charts=server.charts)  # fmt: skip
    server.hub = server.app.state.hub
    server.hub.candle_interval_s = 0.2
    await server.start()
    yield server
    await server.stop()


async def test_the_chart_api(live):
    client = await live.session()
    spec = (await client.get("/api/charts/spec")).json()
    assert spec["history"] == "test history" and spec["intervals"][-1]["code"] == "1d"
    live.md.on_tick(tick(t(9, 16, 10), 2500500, 100))
    live.md.on_tick(tick(t(9, 16, 20), 2500600, 175))
    url = f"/api/charts/{FUT}/candles"
    got = (await client.get(url, params={"interval": "1m", "start": "2026-10-16T09:00:00",
                                         "end": "2026-10-16T10:00:00+05:30"})).json()  # fmt: skip
    assert [(r["t"], r["o"], r["c"], r["v"]) for r in got["candles"]] == [
        ("2026-10-16T09:15:00+05:30", 2500000, 2500001, 10),  # history
        ("2026-10-16T09:16:00+05:30", 2500500, 2500600, 75),  # live (partial: no history there)
    ]
    assert got["interval"] == "1m" and got["live_from"] == "2026-10-16T09:17:00+05:30"
    default = (await client.get(url)).json()  # the day up to now (the machine's clock)
    assert default["interval"] == "1m"
    bad = await client.get(url, params={"interval": "7m"})
    assert bad.status_code == 422 and "interval must be one of" in bad.json()["detail"]
    assert (await client.get(url, params={"start": "yesterday"})).status_code == 422
    assert (await client.get("/api/charts/NFO:NOPE/candles")).status_code == 404
    marks = await client.get(f"/api/accounts/acc-ayush/charts/{FUT}/marks")
    assert marks.status_code == 200 and marks.json()["marks"] == []
    assert (await client.get(f"/api/accounts/acc-riya/charts/{FUT}/marks")).status_code == 404
    await client.aclose()


async def test_the_forming_candle_streams_at_most_once_a_second(live, monkeypatch):
    from test_stream import all_of, next_of

    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": "5m"}))
        first = await next_of(ws, "candle")
        assert first["interval"] == "5m" and first["candle"] is None  # nothing built yet
        for n in range(20):  # a burst for 0.6 s
            live.md.on_tick(tick(t(9, 20, 1 + n), 2501000 + 10 * n, 1000 + 75 * n))
            await asyncio.sleep(0.03)
        updates = await all_of(ws, "candle", quiet=0.5)
        assert 2 <= len(updates) <= 5  # throttled
        last = updates[-1]["candle"]
        assert (last["t"], last["o"], last["c"], last["v"]) == (
            "2026-10-16T09:20:00+05:30",
            2501000,
            2501190,
            1425,
        )
        await ws.send(json.dumps({"op": "candles_stop", "instrument_id": FUT, "interval": "5m"}))
        await asyncio.sleep(0.1)
        live.md.on_tick(tick(t(9, 21), 2502000, 3000))
        assert await all_of(ws, "candle", quiet=0.5) == []
        await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": "2m"}))
        assert "interval must be one of" in (await next_of(ws, "error"))["message"]
        monkeypatch.setattr("laddrix.server.stream.MAX_CHARTS", 2)
        for code in ("1m", "1m", "5m"):  # the same chart twice replaces it
            await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": code}))
        await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": "1h"}))
        assert (await next_of(ws, "error"))["message"] == "at most 2 charts at a time"
        await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": "2m"}))
        assert "interval must be one of" in (await next_of(ws, "error"))["message"]
        await ws.send(json.dumps({"op": "candles", "instrument_id": "NFO:NOPE", "interval": "1m"}))
        assert "unknown instrument NFO:NOPE" in (await next_of(ws, "error"))["message"]
    await client.aclose()
