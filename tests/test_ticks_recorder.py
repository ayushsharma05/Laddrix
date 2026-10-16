"""Recorded ticks (K11.1, #223): what is written is read back to the field, a crash loses at most
the last flush, old days are rotated, and the trading loop never waits for the disk."""

import asyncio
import gzip
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.research.ticks import FileTickStore, TickRecorder, decode, encode

IST = ZoneInfo("Asia/Kolkata")
FUT = "NFO:NIFTY26OCTFUT"
REL = "NSE:RELIANCE"
T0 = datetime(2026, 10, 16, 9, 15, tzinfo=IST)


def full(instrument=FUT, ltp=2501000, at=T0):
    """A tick with every field set, including depth and three different time zones."""
    return Tick(
        instrument, ltp, received_at=at, last_qty=75, avg_price_paise=2500512, volume=1_234_575,
        buy_qty=987_000, sell_qty=876_000, open_paise=2499000, high_paise=2503000,
        low_paise=2497050, close_paise=2490000, open_interest=12_345_600,
        last_trade_time=at.astimezone(UTC) - timedelta(microseconds=7),
        exchange_time=at.astimezone(timezone(timedelta(hours=-5))),
        bids=tuple(DepthLevel(ltp - 10 * i, 75 * (i + 1), i + 1) for i in range(5)),
        asks=tuple(DepthLevel(ltp + 10 * (i + 1), 150, 2) for i in range(5)),
    )  # fmt: skip


def test_a_tick_round_trips_to_the_field():
    tick = full()
    back, recorded = decode(encode(tick, T0 + timedelta(microseconds=3)))
    assert back == tick  # every field, depth included; times equal as instants
    assert back.received_at.utcoffset() == timedelta(0)  # read back in UTC
    assert recorded == int((T0 - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 10**6 + 3
    light = Tick(REL, 250000)  # a light-mode tick: the missing fields stay missing
    assert decode(encode(light, T0))[0] == light


def test_a_day_is_written_per_segment_and_read_back_in_order(tmp_path):
    store = FileTickStore(tmp_path)
    first = [(full(FUT, 2501000 + i * 10, T0 + timedelta(seconds=i)), T0 + timedelta(seconds=i))
             for i in range(3)]  # fmt: skip
    second = [(full(REL, 250000 + i, T0 + timedelta(seconds=i, milliseconds=500)),
               T0 + timedelta(seconds=i, milliseconds=500)) for i in range(3)]  # fmt: skip
    store.append(first + second)
    store.append([(full(FUT, 2502000, T0 + timedelta(seconds=9)), T0 + timedelta(seconds=9))])
    day = T0.date()
    assert store.days() == [day]
    ticks = list(store.read(day))
    assert [t.instrument_id for t in ticks] == [FUT, REL, FUT, REL, FUT, REL, FUT]  # by time
    assert ticks[-1].ltp_paise == 2502000
    assert [t.ltp_paise for t in store.read(day, [REL])] == [250000, 250001, 250002]
    s = store.summary(day)
    assert s["ticks"] == 7 and s["instruments"] == {FUT: 4, REL: 3}
    assert set(s["segments"]) == {"NFO", "NSE"} and s["bytes"] == sum(s["segments"].values())


def test_the_india_day_decides_the_file(tmp_path):
    store = FileTickStore(tmp_path)
    late = datetime(2026, 10, 16, 23, 59, tzinfo=IST)  # still the 16th in India (18:29 UTC)
    store.append([(full(at=late), late), (full(at=late + timedelta(minutes=2)),
                                          late + timedelta(minutes=2))])  # fmt: skip
    assert store.days() == [date(2026, 10, 16), date(2026, 10, 17)]


def test_a_crash_mid_write_loses_only_the_last_flush(tmp_path):
    store = FileTickStore(tmp_path)
    store.append([(full(ltp=2501000), T0)])
    store.append([(full(ltp=2501010), T0 + timedelta(seconds=1))])
    path = tmp_path / T0.date().isoformat() / "NFO.ticks.gz"
    whole = path.read_bytes()
    third = gzip.compress(b"x" * 10_000)
    path.write_bytes(whole + third[: len(third) // 2])  # the third flush cut short
    assert [t.ltp_paise for t in store.read(T0.date())] == [2501000, 2501010]


def test_rotation_keeps_the_newest_days(tmp_path):
    store = FileTickStore(tmp_path, keep_days=2)
    for n in range(4):
        at = T0 + timedelta(days=n)
        store.append([(full(at=at), at)])
    (tmp_path / "not-a-day").mkdir()  # other folders are left alone
    gone = store.rotate()
    assert gone == [date(2026, 10, 16), date(2026, 10, 17)]
    assert store.days() == [date(2026, 10, 18), date(2026, 10, 19)]
    assert (tmp_path / "not-a-day").is_dir()
    assert FileTickStore(tmp_path, keep_days=0).rotate() == []  # 0: keep everything


async def test_the_recorder_takes_every_tick_and_writes_them_off_the_loop(tmp_path):
    md = MarketData()
    store = FileTickStore(tmp_path)
    clock = iter(T0 + timedelta(milliseconds=n) for n in range(100))
    recorder = TickRecorder(store, flush_s=0.01, clock=lambda: next(clock),
                            record=lambda i: i != REL)  # fmt: skip
    md.tap(recorder.on_tick)
    run = asyncio.create_task(recorder.run())
    for n in range(5):
        md.on_tick(full(ltp=2501000 + n))  # nobody subscribes: recorded all the same
    md.on_tick(full(REL))  # not chosen: not recorded
    async with asyncio.timeout(2):
        while recorder.recorded < 5:
            await asyncio.sleep(0.01)
    md.on_tick(full(ltp=2509999))
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)  # the last ticks written on the way out
    assert [t.ltp_paise for t in store.read(T0.date())] == [2501000, 2501001, 2501002, 2501003,
                                                             2501004, 2509999]  # fmt: skip


async def test_a_failed_write_keeps_the_ticks_for_the_next_flush(tmp_path):
    class Flaky(FileTickStore):
        fail = True

        def append(self, batch):
            if self.fail:
                self.fail = False
                raise OSError("disk full")
            super().append(batch)

    store = Flaky(tmp_path)
    recorder = TickRecorder(store, clock=lambda: T0)
    recorder.on_tick(full(ltp=1))
    await recorder.flush()
    assert recorder.failures == 1 and recorder.recorded == 0
    recorder.on_tick(full(ltp=2))
    await recorder.flush()
    assert [t.ltp_paise for t in store.read(T0.date())] == [1, 2]  # kept, in order


async def test_the_recordings_api(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    store = FileTickStore(tmp_path / "ticks", keep_days=30)
    store.append([(full(), T0), (full(REL), T0)])
    c = await w.logged_in()
    assert (await c.get("/api/research/recordings")).status_code == 404  # not recording
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), ticks=store)
    c = await w.logged_in()
    listing = (await c.get("/api/research/recordings")).json()
    assert listing["keep_days"] == 30
    assert [(d["day"], d["ticks"], d["instruments"]) for d in listing["days"]] == [
        ("2026-10-16", 2, 2)]  # fmt: skip
    day = (await c.get("/api/research/recordings/2026-10-16")).json()
    assert day["instruments"] == {FUT: 1, REL: 1}
    assert (await c.get("/api/research/recordings/2026-10-17")).status_code == 404
    assert (await c.get("/api/research/recordings/today")).status_code == 422
    await c.aclose()
    w.log.close()


@pytest.mark.parametrize("value, ok", [("0", True), ("7", True), ("-1", False), ("x", False)])
def test_the_recording_setting(value, ok):
    from test_serve import env

    from mocktrade.config import ConfigError
    from mocktrade.tools.serve import ServeSettings

    if ok:
        assert ServeSettings.from_env(env(MOCKTRADE_RECORD_DAYS=value)).record_days == int(value)
    else:
        with pytest.raises(ConfigError, match="MOCKTRADE_RECORD_DAYS"):
            ServeSettings.from_env(env(MOCKTRADE_RECORD_DAYS=value))


def test_a_day_is_deleted_on_request_but_never_today(tmp_path):
    now = T0 + timedelta(days=2)
    store = FileTickStore(tmp_path, clock=lambda: now)
    for n in range(3):
        at = T0 + timedelta(days=n)
        store.append([(full(at=at), at)])
    store.delete(date(2026, 10, 16))
    assert store.days() == [date(2026, 10, 17), date(2026, 10, 18)]
    with pytest.raises(ValueError, match="2026-10-18 is today: it is still being recorded"):
        store.delete(date(2026, 10, 18))
    with pytest.raises(LookupError, match="nothing recorded on 2026-10-16"):
        store.delete(date(2026, 10, 16))
    late = datetime(2026, 10, 18, 23, 0, tzinfo=UTC)  # already the 19th in India
    FileTickStore(tmp_path, clock=lambda: late).delete(date(2026, 10, 18))
    assert store.days() == [date(2026, 10, 17)]


async def test_deleting_a_recorded_day_and_the_replay_command(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    store = FileTickStore(tmp_path / "ticks", clock=lambda: T0 + timedelta(days=1))
    for at in (T0, T0 + timedelta(days=1)):
        store.append([(full(at=at), at)])
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), ticks=store,
                       replay_command=lambda d: f"replay {d}")  # fmt: skip
    trader = await w.logged_in("riya")
    listing = (await trader.get("/api/research/recordings")).json()
    assert [d["deletable"] for d in listing["days"]] == [False, False]  # not an admin
    assert [d["replay"] for d in listing["days"]] == ["replay 2026-10-17", "replay 2026-10-16"]
    r = await trader.delete("/api/research/recordings/2026-10-16")
    assert r.status_code == 403 and "only an admin" in r.json()["detail"]
    admin = await w.logged_in("ayush")
    seen = (await admin.get("/api/research/recordings")).json()["days"]
    assert [(d["day"], d["deletable"]) for d in seen] == [("2026-10-17", False),
                                                          ("2026-10-16", True)]  # fmt: skip
    today = await admin.delete("/api/research/recordings/2026-10-17")
    assert today.status_code == 409 and "still being recorded" in today.json()["detail"]
    ok = await admin.delete("/api/research/recordings/2026-10-16")
    assert ok.status_code == 200 and ok.json() == {"day": "2026-10-16", "deleted": True}
    assert store.days() == [date(2026, 10, 17)]
    again = await admin.delete("/api/research/recordings/2026-10-16")
    assert again.status_code == 404
    assert (await admin.delete("/api/research/recordings/yesterday")).status_code == 422
    await w.log.flush()
    audit = w.log.notes("-", ["RECORDING_DELETED"])
    assert len(audit) == 1 and audit[0].detail.startswith("ayush deleted the ticks of 2026-10-16")
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), ticks=store)
    plain = await w.logged_in("ayush")
    assert "replay" not in (await plain.get("/api/research/recordings")).json()["days"][0]
    for c in (trader, admin, plain):
        await c.aclose()
    w.log.close()
