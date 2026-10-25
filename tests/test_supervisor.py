"""Critical loops watched, and one trading server per data folder (#273).

The failures before:
- every background loop ran in one `gather`: one that crashed ended silently, and the web
  server kept answering "ok" while fills, reconciling or recovery had stopped;
- nothing stopped a second server from starting on the same data and trading the same
  accounts as the same owner.
"""

import asyncio
import os
import signal
import subprocess
import sys

import pytest

from laddrix.ops.fence import DataFence, FenceError
from laddrix.ops.supervisor import BACKOFF_S, PauseGate, Supervisor


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s
        await asyncio.sleep(0)


def watched():
    clock, alerts = Clock(), []
    sup = Supervisor(alert=lambda k, lvl, t, a: alerts.append((k, lvl, t, a)), monotonic=clock,
                     sleep=clock.sleep)  # fmt: skip
    return sup, clock, alerts


async def test_a_crashed_loop_is_said_restarted_and_pauses_new_orders_meanwhile():
    sup, clock, alerts = watched()
    runs, gate = [], PauseGate(sup, "acc")
    seen_paused = []

    async def updates():
        runs.append(1)
        if len(runs) == 1:
            raise ConnectionError("socket closed")
        seen_paused.append(await gate.admit_new(None))  # running again: open
        await asyncio.Event().wait()

    real_sleep = clock.sleep

    async def sleeping(s):  # while it waits to restart: new orders paused, cancels go
        seen_paused.append(await gate.admit_new(None))
        assert await gate.admit_cancel(None) is None
        await real_sleep(s)

    sup._sleep = sleeping
    task = asyncio.create_task(sup.keep("acc: broker updates", updates, account_id="acc"))
    await asyncio.sleep(0.01)
    assert len(runs) == 2 and clock.slept == [BACKOFF_S[0]]
    assert seen_paused[0].startswith("acc: broker updates is restarting (ConnectionError: socket "
                                     "closed): new orders wait; cancels go")  # fmt: skip
    assert seen_paused[1] is None
    key = "loop:acc: broker updates"
    assert [(k, lvl) for k, lvl, _, _ in alerts] == [(key, "ERROR"), (key, "OK")]
    assert alerts[0][3] == "acc"  # the account's alert: only its people see it
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_loop_that_keeps_failing_is_quarantined_and_stays_paused():
    sup, clock, alerts = watched()
    runs = []

    async def broken():
        runs.append(1)
        raise RuntimeError("bad state")

    await sup.keep("acc: reconciling positions", broken, account_id="acc")  # returns: gave up
    assert len(runs) == 5 and sup.loops["acc: reconciling positions"].state == "quarantined"
    assert clock.slept == list(BACKOFF_S[:4])  # 1, 2, 4, 8 s between tries
    assert alerts[-1][1] == "ERROR" and "keeps failing" in alerts[-1][2]
    assert "stopped after repeated failures" in sup.paused("acc")
    assert sup.paused("other") is None  # another account trades on


async def test_a_one_shot_that_finishes_is_done_and_a_side_loop_pauses_nothing():
    sup, clock, _ = watched()

    async def recovery():
        return "ok"

    await sup.keep("acc: recovery", recovery, account_id="acc")
    assert sup.loops["acc: recovery"].state == "done"

    async def flaky():
        raise OSError("disk")

    sup.loops.clear()
    task = asyncio.create_task(sup.keep("funds", flaky, critical=False, account_id="acc"))
    await asyncio.sleep(0)
    assert sup.paused("acc") is None  # not critical: said, but nothing waits for it
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_ready_needs_the_critical_loops_and_a_ticking_watchdog():
    sup, clock, _ = watched()
    assert sup.ready() == (False, ["the supervisor's watchdog isn't ticking"])
    dog = asyncio.create_task(sup.watchdog())
    await asyncio.sleep(0)
    assert sup.ready() == (True, [])
    dog.cancel()
    await asyncio.gather(dog, return_exceptions=True)
    clock.t += 60  # the supervisor stuck: its heartbeat goes stale
    assert sup.ready()[0] is False


async def test_a_platform_loop_failing_pauses_that_accounts_orders_then_recovers(
    nifty_fut, monkeypatch
):
    """The real wiring: the account's broker-updates loop really crashes (once). Until the
    supervisor has restarted it (1 s later), new orders are refused with the reason; cancels
    aren't; afterwards orders go again and fills arrive as before."""
    from test_portfolio import FUT, Paper

    from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
    from laddrix.orders.manager import OrderManager

    real = OrderManager.run
    crashes = []

    async def dies_once(self):
        if not crashes:
            crashes.append(1)
            raise RuntimeError("update stream broke")
        await real(self)

    monkeypatch.setattr(OrderManager, "run", dies_once)
    p = Paper(nifty_fut)

    def order():
        return p.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75,
                                               OrderType.LIMIT, Product.NRML,
                                               price_paise=2501010))  # fmt: skip

    try:
        await asyncio.sleep(0.05)
        assert crashes
        refused = await order()
        assert refused.status is OrderStatus.REJECTED
        assert refused.reason == ("acc: broker updates is restarting (RuntimeError: update stream "
                                  "broke): new orders wait; cancels go")  # fmt: skip
        assert not p.platform.supervisor.ready()[0]
        async with asyncio.timeout(5):
            while p.platform.supervisor.paused("acc"):
                await asyncio.sleep(0.05)
        await p.buy(FUT, 75, 2501010, Product.NRML)  # orders go and fills arrive again
        assert p.rt.positions.net_qty(FUT) == 75
    finally:
        await p.stop()


async def test_ready_is_its_own_endpoint_apart_from_health(tmp_path, nifty_fut):
    from test_server import World

    w = World(tmp_path, nifty_fut)
    c = w.client()
    assert (await c.get("/api/health")).status_code == 200  # alive
    r = await c.get("/api/ready")
    assert r.status_code == 503 and r.json()["ready"] is False  # not trading-ready yet
    dog = asyncio.create_task(w.platform.supervisor.watchdog())
    await asyncio.sleep(0)
    assert (await c.get("/api/ready")).json() == {"ready": True}
    dog.cancel()
    await asyncio.gather(dog, return_exceptions=True)
    await c.aclose()
    w.log.close()


# --- one server per data folder -----------------------------------------------------------------


def test_a_second_server_on_the_same_data_is_refused(tmp_path):
    first = DataFence(tmp_path)
    first.acquire()
    with pytest.raises(
        FenceError, match=f"another Laddrix server owns {tmp_path} \\(pid {os.getpid()}"
    ):
        DataFence(tmp_path).acquire()
    first.release()
    second = DataFence(tmp_path)
    second.acquire()  # the first stopped: the next may start
    second.release()


HOLD = """
import sys, time
sys.path.insert(0, "src")
from pathlib import Path
from laddrix.ops.fence import DataFence
DataFence(Path(sys.argv[1])).acquire()
print("held", flush=True)
time.sleep(60)
"""


def test_a_paused_owner_keeps_the_data_and_a_dead_one_frees_it(tmp_path):
    """A hung server (paused) might wake up and send: nothing takes over from it. One that
    died (killed, crashed) frees the data at once: the next server starts."""
    owner = subprocess.Popen([sys.executable, "-c", HOLD, str(tmp_path)], stdout=subprocess.PIPE)
    try:
        assert owner.stdout.readline().strip() == b"held"
        owner.send_signal(signal.SIGSTOP)  # hung
        with pytest.raises(FenceError, match=f"pid {owner.pid}"):
            DataFence(tmp_path).acquire()
        owner.kill()
        owner.wait(5)
        taker = DataFence(tmp_path)
        taker.acquire()
        taker.release()
    finally:
        if owner.poll() is None:
            owner.kill()


def test_the_server_refuses_to_start_on_owned_data(tmp_path):
    from test_serve import env

    from laddrix.config import ConfigError
    from laddrix.tools.serve import ServeSettings, build

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html></html>")
    held = DataFence(tmp_path / "data")
    held.acquire()
    try:
        with pytest.raises(ConfigError, match="another Laddrix server owns"):
            build(ServeSettings.from_env(env(LADDRIX_DATA=str(tmp_path / "data"),
                                             LADDRIX_UI=str(ui))))  # fmt: skip
    finally:
        held.release()


async def test_shutdown_drains_the_audit_trail_then_frees_the_data(tmp_path):
    """On stop: everything written is on disk before the data folder is let go, so the next
    server (or a restore) starts from all of it."""
    from test_serve import env

    from laddrix.store.events import EventLog
    from laddrix.tools.serve import ServeSettings, build

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html></html>")
    data = tmp_path / "data"
    built = build(ServeSettings.from_env(env(LADDRIX_DATA=str(data), LADDRIX_UI=str(ui))))
    for n in range(200):
        built.log.record_note("DRILL", "-", f"note {n}")  # queued, not yet written
    await built.close()
    after = DataFence(data)
    after.acquire()  # free: the old server let go
    after.release()
    check = EventLog(data / "events.db")
    assert len([e for e in check.events(limit=1000) if e.kind == "DRILL"]) == 200
    check.close()
