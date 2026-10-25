"""A spread's chart (#429): candles of its mark from its legs' candles, gaps shown honestly.

Hand-worked, a calendar OCT − NOV and a fly OCT − 2·NOV + DEC (paise):
    09:15  OCT o 25,000.00 c 25,010.00   NOV o 25,140.00 c 25,130.00   DEC o 25,270.00 c 25,280.00
    09:16  OCT o 25,010.00 c 25,020.00   NOV (no candle)
    calendar 09:15: o −140.00, c −120.00 (high −120.00, low −140.00: the open's and close's);
             09:16: a gap, never filled in
    fly 09:15: o 25,000 − 2·25,140 + 25,270 = −10.00, c 25,010 − 2·25,130 + 25,280 = +30.00
"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from test_charts import FakeHistory, t, tick

from laddrix.charts.candles import CandleBuilder
from laddrix.charts.service import SPREAD_NOTE, Charts, combine, spread_candles, spread_forming
from laddrix.domain.accounts import User
from laddrix.spreads.model import SpreadDef, SpreadLeg
from laddrix.spreads.store import SpreadBook

OCT, NOV, DEC = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT", "NFO:NIFTY26DECFUT"


def k(hh, mm, o, c):
    return {"t": t(hh, mm).isoformat(), "o": o, "h": max(o, c), "l": min(o, c), "c": c, "v": 10,
            "oi": None}  # fmt: skip


LEGS = {
    OCT: [k(9, 15, 2500000, 2501000), k(9, 16, 2501000, 2502000)],
    NOV: [k(9, 15, 2514000, 2513000)],
    DEC: [k(9, 15, 2527000, 2528000), k(9, 16, 2528000, 2528000)],
}


class FakeCharts:
    """Each leg's candles as a chart would answer for it."""

    def __init__(self, legs=LEGS, forming=None, live_from=None, note=None):
        self.legs, self._forming = legs, forming or {}
        self.live_from, self.note = live_from or {}, note
        self.asked = []

    async def candles(self, instrument_id, code, start=None, end=None):
        self.asked.append((instrument_id, code))
        return {"instrument_id": instrument_id, "interval": code,
                "candles": self.legs.get(instrument_id, []),
                "live_from": self.live_from.get(instrument_id), "note": self.note}  # fmt: skip

    def forming(self, instrument_id, code):
        return self._forming.get(instrument_id)


def spread(legs, name="cal"):
    now = datetime(2026, 10, 16, tzinfo=UTC)
    return SpreadDef("s1", "ayush", "", name, tuple(legs), now, now)


CAL = [SpreadLeg(OCT, 1, 1, quote=True), SpreadLeg(NOV, -1, -1)]
FLY = [SpreadLeg(OCT, 1, 1, quote=True), SpreadLeg(NOV, -2, -2), SpreadLeg(DEC, 1, 1)]


async def test_a_calendars_candles_from_its_legs_with_a_gap_where_a_leg_had_none():
    got = await spread_candles(FakeCharts(), spread(CAL), "1m")
    assert [(r["t"][11:16], r["o"], r["h"], r["l"], r["c"], r["v"]) for r in got["candles"]] == [
        ("09:15", -14000, -12000, -14000, -12000, 0)]  # fmt: skip
    assert got["gaps"] == 1  # 09:16: NOV had no candle
    assert (got["instrument_id"], got["kind"], got["approximate_high_low"]) == (
        "SPREAD:s1", "spread mark", True)  # fmt: skip
    assert got["label"] == SPREAD_NOTE


async def test_a_minus_two_leg_counts_twice():
    got = await spread_candles(FakeCharts(), spread(FLY), "1m")
    assert [(r["o"], r["c"]) for r in got["candles"]] == [(-1000, 3000)]


async def test_live_only_once_every_leg_is_and_the_legs_notes_are_kept():
    lives = {OCT: t(9, 17).isoformat(), NOV: t(9, 16).isoformat()}
    got = await spread_candles(FakeCharts(live_from=lives, note="history down"), spread(CAL), "1m")
    assert got["live_from"] == t(9, 17).isoformat() and got["note"] == "history down; history down"
    half = await spread_candles(
        FakeCharts(live_from={OCT: t(9, 17).isoformat()}), spread(CAL), "1m"
    )
    assert half["live_from"] is None


async def test_the_legs_are_fetched_together_not_one_after_another():
    class Slow(FakeCharts):
        async def candles(self, instrument_id, code, start=None, end=None):
            self.asked.append(instrument_id)
            await asyncio.sleep(0)  # a history read: the other legs are asked meanwhile
            self.asked.append(f"done {instrument_id}")
            return await super().candles(instrument_id, code, start, end)

    charts = Slow()
    await spread_candles(charts, spread(FLY), "1m")
    assert charts.asked[:3] == [OCT, NOV, DEC]


def test_the_forming_candle_needs_every_leg_in_the_same_bucket():
    charts = FakeCharts(forming={OCT: k(9, 20, 2501000, 2501500), NOV: k(9, 20, 2514000, 2513500)})
    assert spread_forming(charts, spread(CAL), "5m") == {
        "t": t(9, 20).isoformat(), "o": -13000, "h": -12000, "l": -13000, "c": -12000, "v": 0,
        "oi": None}  # fmt: skip
    assert combine([(1, k(9, 20, 1, 2)), (-1, k(9, 21, 1, 2))]) is None  # different buckets
    assert combine([(1, k(9, 20, 1, 2)), (-1, None)]) is None  # a leg has none yet


@pytest.fixture
async def live(tmp_path, nifty_fut):
    """A running server with charts and spreads: the builder taps every tick."""
    from test_stream import Live

    from laddrix.server.app import ServerSettings, create_app

    server = Live(tmp_path, nifty_fut)
    server.store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
    builder = CandleBuilder()
    server.md.tap(builder.on_tick)
    server.charts = Charts(builder, FakeHistory([]), events=None)
    server.book = SpreadBook(None, server.store.get)
    server.app = create_app(server.platform, server.users,
                            ServerSettings(origin=server.origin, cookie_secure=False),
                            charts=server.charts, spreads=server.book)  # fmt: skip
    server.hub = server.app.state.hub
    server.hub.candle_interval_s = 0.2
    await server.start()
    yield server
    await server.stop()


async def test_the_chart_api_answers_a_spread_for_its_owner_only(live):
    from test_stream import next_of

    made = live.book.create(User("ayush", "Ayush"), "cal", [SpreadLeg(OCT, 1, 1, quote=True),
                                                            SpreadLeg(NOV, -1, -1)])  # fmt: skip
    for at, o, n in ((t(9, 16, 10), 2500500, 2514000), (t(9, 16, 40), 2500700, 2514100)):
        live.md.on_tick(tick(at, o, 100))
        live.md.on_tick(tick(at, n, 100, instrument=NOV))
    client = await live.session()
    url = f"/api/charts/{made.instrument_id}/candles"
    got = (await client.get(url, params={"interval": "1m", "start": "2026-10-16T09:00:00",
                                         "end": "2026-10-16T10:00:00"})).json()  # fmt: skip
    assert [(r["t"][11:16], r["o"], r["c"]) for r in got["candles"]] == [
        ("09:16", 2500500 - 2514000, 2500700 - 2514100)]  # fmt: skip
    assert got["kind"] == "spread mark"
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "candles", "instrument_id": made.instrument_id,
                                  "interval": "5m"}))  # fmt: skip
        first = await next_of(ws, "candle")
        assert first["candle"]["o"] == 2500500 - 2514000  # the legs' forming candles together
    riya = await live.session("riya")
    assert (await riya.get(url)).status_code == 404  # not her spread
    async with live.stream(riya) as ws:
        await ws.send(json.dumps({"op": "candles", "instrument_id": made.instrument_id,
                                  "interval": "5m"}))  # fmt: skip
        error = await next_of(ws, "error")
        assert error["message"] == f"unknown instrument {made.instrument_id}"
    await client.aclose()
    await riya.aclose()
    await asyncio.sleep(0)
