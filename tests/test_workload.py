# ruff: noqa: F811 (the `store` fixture, imported)
"""Research and report work never holds up trading (#274).

a backtest  ─► its own process (Worker): a memory cap, a time limit per day, cancel,
               at most MAX_WAITING waiting (MAX_PER_USER each), the same result as in-process
its orders  ─► the order rate counted in replayed time: the result never depends on the
               computer's speed or what else the loop is doing
reports     ─► built in a worker thread
the jobs    ─► each in a task of its own: a slow one never delays the others
"""

import asyncio
import os
import resource
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from test_backtest import DAY, MODEL, T0, spec, store  # noqa: F401 (fixture)
from test_server import ORIGIN
from test_workspaces import TwoFirms

from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.ops.scheduler import IST, Scheduler
from laddrix.reports.service import Reports
from laddrix.reports.store import ReportStore
from laddrix.research import backtest
from laddrix.research.backtest import (
    MAX_PER_USER,
    MAX_WAITING,
    BacktestBusy,
    BacktestError,
    Backtests,
    InProcess,
    RecordedDay,
    Worker,
)
from laddrix.research.ticks import FileTickStore
from laddrix.server.app import ServerSettings, create_app

# a slicer firing a slice a second for a minute: more than the order rate allows, so the
# throttle makes orders wait their turn (in replayed time)
FAST = {"slices": 60, "interval_s": 1, "mode": "cross"}


def record(root, fut, n=6000):
    """n ticks, 200 ms apart from 09:15, the price wandering within ±20 ticks."""
    s = FileTickStore(root)
    batch = []
    for i in range(n):
        at = T0 + timedelta(milliseconds=200 * i)
        ltp = 2500000 + 10 * ((i * 7) % 41 - 20)
        batch.append((Tick(fut, ltp, received_at=at, bids=(DepthLevel(ltp - 10, 7500, 3),),
                           asks=(DepthLevel(ltp + 10, 7500, 3),)), at))  # fmt: skip
    s.append(batch)
    return RecordedDay(root, DAY)


async def busy_loop(stop):
    """Other work on the loop, as on a server: wakes often, holds the loop a little."""
    while not stop.is_set():
        end = time.perf_counter() + 0.00002  # 20 µs: other work, without slowing the test
        while time.perf_counter() < end:
            pass
        await asyncio.sleep(0)


async def test_a_backtest_never_depends_on_what_else_the_loop_is_doing(nifty_fut, store, tmp_path):
    day = record(tmp_path / "ticks", nifty_fut.id, n=1500)  # 5 minutes of replayed time
    run = spec(nifty_fut.id, qty=60 * 75, params=FAST)
    alone = await InProcess().day(run, DAY, day, store, MODEL, tmp_path / "a")
    stop = asyncio.Event()
    noise = asyncio.create_task(busy_loop(stop))
    crowded = await InProcess().day(run, DAY, day, store, MODEL, tmp_path / "b")
    stop.set()
    await noise
    assert len(alone["trades"]) == 60  # every slice, each waiting its turn when it must
    assert crowded["totals"] == alone["totals"] and crowded["trades"] == alone["trades"]


async def test_an_order_waits_its_turn_in_replayed_time(
    nifty_fut, store, tmp_path, monkeypatch, caplog
):
    """A tighter order rate (1 per 5 s): each slice waits for the window in replayed time, and
    the replay goes on meanwhile, never stuck re-checking a waiting order."""
    import functools

    from laddrix.risk.throttle import OrderThrottle, RateRule

    tight = (RateRule("per 5 s", 5.0, limit=1, cancel_limit=10),)
    monkeypatch.setattr(backtest, "OrderThrottle", functools.partial(OrderThrottle, rules=tight))
    day = record(tmp_path / "ticks", nifty_fut.id)
    run = spec(nifty_fut.id, qty=10 * 75, params={"slices": 10, "interval_s": 1, "mode": "cross"})
    started = time.perf_counter()
    report = await InProcess().day(run, DAY, day, store, MODEL, tmp_path / "a")
    times = [datetime.fromisoformat(t["ts"]) for t in report["trades"]]
    assert len(times) == 10
    # sent at least 5 s apart; each fills on the next tick, so fills are at least 4.5 s apart
    assert all((b - a).total_seconds() >= 4.5 for a, b in zip(times, times[1:], strict=False))
    assert not [r for r in caplog.records if "still busy" in r.getMessage()]
    assert time.perf_counter() - started < 10  # the waits are replayed time, not real


async def test_the_worker_gives_the_same_result_and_leaves_the_loop_free(
    nifty_fut, store, tmp_path
):
    day = record(tmp_path / "ticks", nifty_fut.id, n=20000)
    run = spec(nifty_fut.id, qty=60 * 75, params=FAST)
    here = await InProcess().day(run, DAY, day, store, MODEL, tmp_path / "a")
    worker = Worker()
    lags, stop = [], asyncio.Event()

    async def probe():
        while not stop.is_set():
            asked = time.perf_counter()
            await asyncio.sleep(0.005)
            lags.append(time.perf_counter() - asked - 0.005)

    p = asyncio.create_task(probe())
    try:
        there = await worker.day(run, DAY, day, store, MODEL, tmp_path / "b")
        pid = next(iter(worker._pool._processes))
    finally:
        stop.set()
        await p
        worker.close()
    assert there["totals"] == here["totals"] and there["trades"] == here["trades"]
    assert pid != os.getpid()  # another process ran it
    assert max(lags) < 0.05  # the loop kept answering: never 50 ms late
    assert worker._pool is None  # closed: the process is gone


async def test_the_worker_has_a_memory_cap(tmp_path):
    worker = Worker(memory=3 << 30)
    try:
        limits = await asyncio.get_running_loop().run_in_executor(
            worker._executor(), resource.getrlimit, resource.RLIMIT_AS
        )
        assert limits == (3 << 30, 3 << 30)
    finally:
        worker.close()


async def test_a_day_over_its_time_limit_is_stopped_and_the_next_one_runs(
    nifty_fut, store, tmp_path
):
    day = record(tmp_path / "ticks", nifty_fut.id)
    run = spec(nifty_fut.id)
    worker = Worker(timeout_s=0.01)  # far less than a process takes to start
    try:
        with pytest.raises(BacktestError, match="took longer than 0 s, stopped"):
            await worker.day(run, DAY, day, store, MODEL, tmp_path / "a")
        assert worker._pool is None  # killed, not left running
        worker._timeout_s = 300
        assert (await worker.day(run, DAY, day, store, MODEL, tmp_path / "b"))["trades"]
    finally:
        worker.close()


async def test_a_worker_that_dies_fails_its_day_not_the_server(nifty_fut, store, tmp_path):
    day = record(tmp_path / "ticks", nifty_fut.id, n=40000)
    run = spec(nifty_fut.id)
    worker = Worker()
    try:
        job = asyncio.ensure_future(worker.day(run, DAY, day, store, MODEL, tmp_path / "a"))
        while not worker._pool or not worker._pool._processes:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.5)
        for process in list(worker._pool._processes.values()):
            process.kill()  # e.g. the kernel's out-of-memory killer
        with pytest.raises(BacktestError, match="the backtest worker stopped"):
            await job
        assert (
            await worker.day(
                run, DAY, record(tmp_path / "t2", nifty_fut.id), store, MODEL, tmp_path / "b"
            )
        )["trades"]  # a fresh worker
    finally:
        worker.close()


async def test_waiting_runs_are_bounded_and_can_be_cancelled(nifty_fut, store, tmp_path):
    day = record(tmp_path / "ticks", nifty_fut.id, n=40000)
    worker = Worker()
    runs = Backtests(tmp_path / "runs", store, MODEL, ticks=lambda _d: day, runner=worker)
    try:
        first = runs.start(spec(nifty_fut.id), owner="ayush")
        second = runs.start(spec(nifty_fut.id), owner="ayush")
        with pytest.raises(BacktestBusy, match=f"already have {MAX_PER_USER} backtests"):
            runs.start(spec(nifty_fut.id), owner="ayush")
        others = [runs.start(spec(nifty_fut.id), owner=f"u{n}")
                  for n in range(MAX_WAITING - 2)]  # fmt: skip
        with pytest.raises(BacktestBusy, match=f"{MAX_WAITING} backtests are already waiting"):
            runs.start(spec(nifty_fut.id), owner="someone-else")
        while first.state != "running" or not worker._pool or not worker._pool._processes:
            await asyncio.sleep(0.01)
        process = next(iter(worker._pool._processes.values()))
        assert await runs.cancel(second.run_id)  # queued: never started
        assert await runs.cancel(first.run_id)  # running: its worker is killed
        assert (first.state, second.state) == ("cancelled", "cancelled")
        process.join(5)
        assert not process.is_alive()
        assert not await runs.cancel(first.run_id)  # already ended
        for r in others:
            await runs.cancel(r.run_id)
        await runs.settle()
        kept = Backtests(tmp_path / "runs", store, MODEL, ticks=lambda _d: None)
        assert kept.get(first.run_id).state == "cancelled"  # read back as it was
    finally:
        runs.close()


async def test_the_cancel_and_busy_answers_of_the_api(tmp_path, nifty_fut):
    w = TwoFirms(tmp_path, nifty_fut)
    gate = asyncio.Event()

    class Held(InProcess):  # a day that waits until told: the run stays running
        async def day(self, *args):
            await gate.wait()
            return {"day": DAY.isoformat(), "skipped": "held"}

    runs = Backtests(tmp_path / "runs", w.platform.instruments, MODEL,
                     ticks=lambda _d: iter([]), runner=Held())  # fmt: skip
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), backtests=runs)
    ayush, ravi, riya = [await w.logged_in(u) for u in ("ayush", "ravi", "riya")]
    body = {"days": [DAY.isoformat()], "algo": "SLICER", "instrument_id": nifty_fut.id,
            "side": "BUY", "qty": 75, "params": {"slices": 1, "interval_s": 60}}  # fmt: skip
    ids = [(await riya.post("/api/research/backtests", json=body)).json()["run_id"]
           for _ in range(MAX_PER_USER)]  # fmt: skip
    busy = await riya.post("/api/research/backtests", json=body)
    assert busy.status_code == 429 and "already have" in busy.json()["detail"]
    assert (await ravi.post(f"/api/research/backtests/{ids[1]}/cancel", json={})).status_code == 404
    r = await riya.post(f"/api/research/backtests/{ids[1]}/cancel", json={})
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    r = await ayush.post(f"/api/research/backtests/{ids[0]}/cancel", json={})  # her firm's admin
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    again = await riya.post(f"/api/research/backtests/{ids[0]}/cancel", json={})
    assert again.status_code == 409 and "already ended (cancelled)" in again.json()["detail"]
    gate.set()
    await w.log.flush()
    assert [n.detail for n in w.log.notes("-", ["BACKTEST"])][-2:] == [
        f"riya cancelled {ids[1]}", f"ayush cancelled {ids[0]}"]  # fmt: skip
    w.log.close()


async def test_a_slow_job_never_holds_up_the_others():
    now = [datetime(2026, 10, 16, 16, 0, tzinfo=IST).astimezone(UTC)]
    s = Scheduler(clock=lambda: now[0])
    slow_gate, slow_runs, fast_runs = asyncio.Event(), [], []

    async def reports():
        slow_runs.append(now[0])
        await slow_gate.wait()

    s.every(60, reports, between=("00:00", "23:59"), name="reports", any_day=True)
    s.every(5, lambda: fast_runs.append(now[0]), between=("00:00", "23:59"), name="guard",
            any_day=True)  # fmt: skip
    started = time.perf_counter()
    for _ in range(13):
        await s.run_due()
        now[0] += timedelta(seconds=5)
    assert len(fast_runs) == 13  # every 5 s, while the slow job never finished
    assert len(slow_runs) == 1  # due again after 60 s, but still running: skipped, not doubled
    assert time.perf_counter() - started < 13 * backtest_wait() + 1
    slow_gate.set()
    await s.run_due()
    await asyncio.sleep(0)
    now[0] += timedelta(seconds=60)
    await s.run_due()
    assert len(slow_runs) == 2  # finished: it runs again when next due


def backtest_wait():
    from laddrix.ops import scheduler

    return scheduler.WAIT_S


async def test_reports_are_built_off_the_trading_loop(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)
    reports = Reports(w.platform, ReportStore(tmp_path / "reports"))
    threads = []
    build = reports._build

    def spy(*args, **kwargs):
        threads.append(threading.current_thread())
        return build(*args, **kwargs)

    reports._build = spy
    await reports.close_of_day()
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), reports=reports)
    c = await w.logged_in()
    today = reports.today()
    assert (await c.get(f"/api/accounts/acc-ayush/reports/{today}?format=pdf")).status_code == 200
    reports.store = None  # a live preview: built, not read
    assert (await c.get(f"/api/accounts/acc-ayush/reports/{today}")).status_code == 200
    assert threads and all(t is not threading.main_thread() for t in threads)
    await c.aclose()
    w.log.close()


def test_the_limits_are_written_down():
    assert backtest.MAX_WAITING == 10 and backtest.MAX_PER_USER == 2
    assert backtest.DAY_TIMEOUT_S == 900 and backtest.WORKER_MEMORY == 2 << 30
    assert date.fromisoformat("2026-10-16") == DAY


async def test_two_runs_in_the_same_second_never_share_an_id(
    nifty_fut, store, tmp_path, monkeypatch
):
    """The random part repeats (#458): the second run still gets its own id, and both wait."""
    picks = iter(["abcd", "abcd", "abcd", "beef"])
    monkeypatch.setattr(backtest.secrets, "token_hex", lambda _n: next(picks))
    clock = lambda: T0  # noqa: E731 (both start in the same second)
    runs = Backtests(tmp_path / "runs", store, MODEL, ticks=lambda _d: None, clock=clock)
    try:
        first = runs.start(spec(nifty_fut.id), owner="a")
        second = runs.start(spec(nifty_fut.id), owner="b")
        assert first.run_id != second.run_id
        assert second.run_id.endswith("BEEF")
        assert {first.run_id, second.run_id} <= set(runs._tasks)
        for r in (first, second):
            await runs.cancel(r.run_id)
    finally:
        runs.close()
