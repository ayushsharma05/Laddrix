"""Replaying a recorded day (K11.2, #224): ticks at the recorded pace (scaled), the clock on the
replayed time, pause/resume/seek, and the same fills as when the day was live."""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_ticks_recorder import full

from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore
from mocktrade.research.replay import MAX_WAIT_S, TURNS, ReplayClock, ReplayFeed
from mocktrade.research.ticks import FileTickStore

IST = ZoneInfo("Asia/Kolkata")
FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 16, 9, 15, tzinfo=IST)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def recorded(tmp_path, gaps=(0, 1, 3, 600, 601)):
    store = FileTickStore(tmp_path)
    store.append([(full(ltp=2501000 + 10 * n, at=at(s)), at(s)) for n, s in enumerate(gaps)])
    return store


class Waits:
    def __init__(self):
        self.waits = []

    async def __call__(self, seconds):
        self.waits.append(seconds)
        await asyncio.sleep(0)


async def test_ticks_come_at_the_recorded_pace_scaled_and_the_clock_follows(tmp_path):
    store = recorded(tmp_path)
    md, clock, waits = MarketData(), ReplayClock(T0), Waits()
    seen = []
    md.tap(lambda t: seen.append((t.ltp_paise, clock())))
    feed = ReplayFeed(lambda: store.read_timed(T0.date()), md, clock, day=T0.date(), speed=10,
                      sleep=waits)  # fmt: skip
    await feed.run()
    assert waits.waits == [0.1, 0.2, MAX_WAIT_S, 0.1]  # 1 s, 2 s, a long gap capped, 1 s; at 10×
    assert seen == [(2501000, at(0)), (2501010, at(1)), (2501020, at(3)), (2501030, at(600)),
                    (2501040, at(601))]  # fmt: skip  # each tick at its recorded time
    assert feed.state == "finished" and feed.fed == 5
    fast = ReplayFeed(lambda: store.read_timed(T0.date()), MarketData(), ReplayClock(T0),
                      day=T0.date(), speed=0, sleep=(w := Waits()))  # fmt: skip
    await fast.run()
    assert w.waits == [0] * 4 * TURNS  # as fast as possible, still letting fills run between ticks


async def test_pause_resume_speed_and_jumping_ahead(tmp_path):
    store = recorded(tmp_path)
    md, clock = MarketData(), ReplayClock(T0)
    feed = ReplayFeed(lambda: store.read_timed(T0.date()), md, clock, day=T0.date(), speed=60)
    states = []
    feed.subscribe(lambda s: states.append(s["state"]))
    with pytest.raises(ValueError, match="speed must be one of 0, 1, 10, 60"):
        feed.set_speed(7)
    run = asyncio.create_task(feed.run())
    await asyncio.sleep(0.01)
    feed.pause()
    fed = feed.fed
    await asyncio.sleep(0.1)
    assert feed.fed == fed and feed.status()["state"] == "paused"  # nothing while paused
    feed.seek(at(600))  # jump over the 10-minute gap: no waiting for it
    with pytest.raises(ValueError, match="only jumps ahead"):
        feed.seek(at(0))
    feed.resume()
    async with asyncio.timeout(1):  # at 60× the gap would take 5 s (the cap); a jump takes none
        await run
    assert feed.state == "finished" and clock() == at(601)
    assert states[:3] == ["playing", "paused", "paused"] and states[-1] == "finished"
    past = ReplayFeed(lambda: store.read_timed(T0.date()), MarketData(), ReplayClock(T0),
                      day=T0.date(), speed=1)  # fmt: skip
    past.seek(at(3600))  # beyond the last tick
    async with asyncio.timeout(1):
        await past.run()
    assert past.status()["seeking_to"] is None and past.state == "finished"


# --- the same fills as when it was live --------------------------------------------------------


def book(ltp):
    return Tick(
        FUT, ltp, bids=(DepthLevel(ltp - 10, 150, 2),), asks=(DepthLevel(ltp + 10, 150, 2),)
    )


async def trade_through(nifty_fut, feed_ticks, *, every_tick=False):
    """A paper broker on the ticks; a buy limit placed after the 2nd tick; the fills it got."""
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    broker = PaperBroker(md, store, latency_s=0.0, every_tick=every_tick)
    broker.start()
    count = 0

    async def place_after_second(tick):
        nonlocal count
        count += 1
        if count == 2:
            await broker.place(OrderRequest("MT1", "acc", "a", FUT, Side.BUY, 225, OrderType.LIMIT,
                                            Product.NRML, price_paise=2500980))  # fmt: skip

    pending = []
    md.tap(lambda t: pending.append(asyncio.ensure_future(place_after_second(t))))
    await feed_ticks(md)
    await asyncio.gather(*pending)
    await broker.settle()
    fills = [(t.qty, t.price_paise) for t in await broker.trades()]
    await broker.aclose()
    return fills


async def test_a_replayed_day_gives_the_same_fills_as_live(tmp_path, nifty_fut):
    prices = [2501000, 2500990, 2500980, 2500970, 2500990, 2500960]
    ticks = [book(p) for p in prices]

    async def live(md):  # the day as it happened: ticks arriving one after another
        for tick in ticks:
            md.on_tick(tick)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    store = FileTickStore(tmp_path)
    store.append([(t, at(n)) for n, t in enumerate(ticks)])  # what the recorder kept

    async def replayed(md):
        feed = ReplayFeed(lambda: store.read_timed(T0.date()), md, ReplayClock(T0),
                          day=T0.date(), speed=0)  # fmt: skip
        await feed.run()
        await asyncio.sleep(0)

    then = await trade_through(nifty_fut, live)
    assert then  # the buy filled while it was live
    # a replay at full speed: the broker sees every tick (a subscription would skip some)
    assert await trade_through(nifty_fut, replayed, every_tick=True) == then
    assert await trade_through(nifty_fut, replayed, every_tick=True) == then  # every time


# --- the replay server -------------------------------------------------------------------------


async def test_the_replay_api(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    c = await w.logged_in()
    assert (await c.get("/api/replay")).status_code == 404  # a normal server
    store = recorded(tmp_path / "ticks")
    feed = ReplayFeed(lambda: store.read_timed(T0.date()), MarketData(), ReplayClock(T0),
                      day=T0.date())  # fmt: skip
    feed.state = "playing"
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), replay=feed)
    c = await w.logged_in()
    assert (await c.get("/api/replay")).json()["day"] == "2026-10-16"
    r = await c.post("/api/replay", json={"action": "pause"})
    assert r.json()["state"] == "paused"
    r = await c.post("/api/replay", json={"action": "speed", "speed": 60})
    assert r.json()["speed"] == 60
    bad = await c.post("/api/replay", json={"action": "speed", "speed": 3})
    assert bad.status_code == 422 and "speed must be one of" in bad.json()["detail"]
    bad = await c.post("/api/replay", json={"action": "seek"})
    assert bad.status_code == 422 and "seek needs a time" in bad.json()["detail"]
    r = await c.post("/api/replay", json={"action": "seek", "at": at(600).isoformat()})
    assert r.json()["seeking_to"] == at(600).isoformat()
    assert (await c.post("/api/replay", json={"action": "rewind"})).status_code == 422
    await w.log.flush()
    assert [e.detail for e in w.log.events(account_id="-") if e.kind == "REPLAY_CONTROL"][0] == (
        "ayush: pause")  # fmt: skip
    await c.aclose()
    w.log.close()


async def test_the_demo_refuses_a_day_that_was_not_recorded(tmp_path, monkeypatch):
    from mocktrade.tools import demo

    monkeypatch.setattr(demo, "REPLAYS", tmp_path / "replay")
    with pytest.raises(SystemExit, match="nothing recorded on 2026-10-16"):
        await demo.main(8999, "http://localhost:8999", None, replay_day=T0.date(),
                        ticks_dir=tmp_path / "none")  # fmt: skip
    from mocktrade.ops.alerts import Alerts

    alerts = Alerts()
    demo._replay_band(alerts, {"day": "2026-10-16", "state": "playing", "speed": 0})
    assert alerts.active()[0].text.startswith("REPLAY of 2026-10-16 (playing, max speed)")
