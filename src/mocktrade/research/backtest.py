"""Backtests: an algo over recorded days, with the same engine, paper broker and risk checks as
live trading, so a backtest can't be kinder than the market was.

    for each day:  a fresh platform (paper broker, risk, algos, positions) on a ReplayClock
                   recorded ticks (or ticks made from candles), as fast as possible, each one:
                     paper broker (every tick) ─► algo engine on_tick ─► positions marked
                     every 100 ms of replayed time: the engine's heartbeat (its timers)
                   at `start` (replayed time): the algo starts
                   end of the day: the K10.3 report (gross, charges, net) at the last prices
    the run:       days in order ─► equity curve (cumulative net), max drawdown, winning days

Deterministic: the same days and spec give the same result, every time. Nothing here sleeps or
reads the wall clock; the order manager's update loop is the only task, and the feed gives it
loop turns between ticks (research/replay.py).

Where it runs (#274): on a server, each day runs in a worker process of its own (`Worker`),
never on the trading loop. The worker has a memory cap, each day a time limit, at most
MAX_WAITING runs wait (MAX_PER_USER per person), and a run can be cancelled. What a day replays
is handed over as a description (`RecordedDay`, `CandleDay`) that the worker opens itself.
Tests run days in-process (`InProcess`), the same code.

Candles (OHLC rows) become ticks O → the far extreme first → the other extreme → C, each with
one tick of spread on either side, so market orders pay the spread and resting orders fill
only when the price trades through them. That path is an assumption, not what traded: a fill
may come out better or worse than the market would have given, and the run says so
(CANDLE_NOTE, #277). Each day also starts on a fresh platform: nothing is carried overnight,
and positions are valued at the day's last price.
"""

import asyncio
import concurrent.futures
import json
import logging
import multiprocessing
import os
import re
import resource
import secrets
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import HEARTBEAT_S, AlgoError
from mocktrade.algos.spec import spec_to_dict
from mocktrade.broker.paper import PaperBroker
from mocktrade.charges.model import ChargesModel
from mocktrade.domain.accounts import MAIN, BrokerAccount
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.reports.daily import build_day, report_to_dict
from mocktrade.research.replay import EPOCH, ReplayClock, ReplayFeed
from mocktrade.research.ticks import IST, FileTickStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.risk.throttle import OrderThrottle
from mocktrade.store.events import EventLog

log = logging.getLogger(__name__)
ACCOUNT = "backtest"
BEAT = timedelta(seconds=HEARTBEAT_S)
DRAIN_TURNS = 200  # far more than one order's path needs (about a dozen)
MAX_DAYS = 250  # about a year of trading days in one run
MAX_WAITING = 10  # runs queued or running on the server; more are refused (#274)
MAX_PER_USER = 2  # of those, one person's
DAY_TIMEOUT_S = 900.0  # one day longer than this is stopped (a worker, #274)
WORKER_MEMORY = 2 << 30  # the worker process's address-space cap, bytes


CANDLE_NOTE = (
    "ticks made from candles: an assumed path (open, the far extreme, the other "
    "extreme, close) with one tick of spread, not what traded. Fills are an "
    "estimate, and can be better or worse than the market would have given"
)


class BacktestError(Exception):
    """A spec that can't run (said in words)."""


class BacktestBusy(BacktestError):
    """Too many backtests waiting: try later (#274)."""


@dataclass(frozen=True)
class BacktestSpec:
    days: tuple[str, ...]  # YYYY-MM-DD, recorded (or with candles)
    algo: str  # an algo code from the algo specs
    instrument_id: str
    side: str  # BUY | SELL
    qty: int
    params: dict[str, Any] = field(default_factory=dict)
    product: str = "NRML"
    start: str = "09:15"  # India time the algo starts each day
    capital_paise: int = 10_00_000_00  # the paper account's money
    name: str = ""

    def problems(self) -> list[str]:
        out = []
        if not self.days:
            out.append("pick at least one day")
        for d in self.days:
            try:
                date.fromisoformat(d)
            except ValueError:
                out.append(f"not a date: {d!r}")
        if self.side not in ("BUY", "SELL"):
            out.append("side must be BUY or SELL")
        if self.qty <= 0:
            out.append("the quantity must be above 0")
        try:
            time.fromisoformat(self.start)
        except ValueError:
            out.append(f"start must be a time like 09:20, not {self.start!r}")
        return out


@dataclass(frozen=True)
class RecordedDay:
    """A recorded day on disk, opened where the day runs (a worker process, #274)."""

    root: Path
    day: date

    def open(self) -> Iterator[tuple[int, Tick]]:
        return FileTickStore(self.root).read_timed(self.day)


@dataclass(frozen=True)
class CandleDay:
    """A day of imported candles, turned into ticks where the day runs."""

    rows: tuple[dict[str, Any], ...]


DayInput = Iterator[tuple[int, Tick]] | RecordedDay | CandleDay
# a day's ticks: an iterator (in-process only), or a description a worker opens itself
TickSource = Callable[[date], Iterator[tuple[int, Tick]] | RecordedDay | None]  # None: nothing
CandleSource = Callable[[str, date], list[dict[str, Any]] | None]  # (instrument, day) -> rows


def candles_to_ticks(
    instrument: Instrument, rows: Iterable[dict[str, Any]]
) -> list[tuple[int, Tick]]:
    """OHLC rows ({"time": ISO, "open", "high", "low", "close" in paise, "volume"}) as ticks,
    adverse path first (see the module notes)."""
    out = []
    t = instrument.tick_paise
    for row in rows:
        at = datetime.fromisoformat(row["time"])
        o, h, low, c = (int(row[k]) for k in ("open", "high", "low", "close"))
        path = [o, low, h, c] if c >= o else [o, h, low, c]
        step = timedelta(seconds=15)
        volume = int(row.get("volume") or 0)
        for n, price in enumerate(path):
            when = at + n * step
            us = (when - EPOCH) // timedelta(microseconds=1)
            tick = Tick(instrument.id, price, received_at=when, volume=volume,
                        bids=(DepthLevel(price - t, 10**9, 1),),
                        asks=(DepthLevel(price + t, 10**9, 1),))  # fmt: skip
            out.append((us, tick))
    return out


async def run_day(
    spec: BacktestSpec,
    day: date,
    ticks: Iterator[tuple[int, Tick]],
    instruments: InstrumentStore,
    charges: ChargesModel,
    folder: Path,
) -> dict[str, Any]:
    """One day on a fresh platform. Returns the K10.3 report plus the intraday equity curve."""
    first = next(ticks, None)
    if first is None:
        return {"day": day.isoformat(), "skipped": "no ticks"}
    clock = ReplayClock(EPOCH + timedelta(microseconds=first[0]))
    md = MarketData()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.db"
    if path.exists():
        path.unlink()
    events = EventLog(path)
    events.start()
    platform = Platform(
        instruments, md, KillSwitch("firm"), events, charges_model=charges, durable_orders=False
    )  # a replay: no real order is ever sent
    broker = PaperBroker(md, instruments, latency_s=0.0, clock=clock, every_tick=True,
                         starting_paise=spec.capital_paise)  # fmt: skip
    asleep = {"n": 0}  # orders waiting for their turn at the order rate, in replayed time

    async def replay_sleep(seconds: float) -> None:
        """The throttle's wait, in replayed time: ticks go on until it has passed (#274)."""
        until = clock() + timedelta(seconds=seconds)
        asleep["n"] += 1
        try:
            while clock() < until:
                await asyncio.sleep(0)
        finally:
            asleep["n"] -= 1

    # the order rate counted in replayed time, never the computer's: a day replayed in
    # seconds would otherwise wait real milliseconds while ticks run on (#274)
    throttle = OrderThrottle(monotonic=lambda: (clock() - EPOCH).total_seconds(),
                             today=lambda: clock().astimezone(IST).date(),
                             sleep=replay_sleep)  # fmt: skip
    rt = platform.add_account(
        BrokerAccount(ACCOUNT, "paper", "BT", "backtest"), broker, ids=ClientIds("BT"),
        algo_types=BUILTIN, clock=clock, throttle=throttle,
        limits=RiskLimits(max_order_value_paise=10**13, max_position_lots=10**4,
                          max_order_lots=10**3, max_daily_loss_paise=10**13),
    )  # fmt: skip
    start_at = datetime.combine(day, time.fromisoformat(spec.start), IST)
    state: dict[str, Any] = {"started": False, "next_beat": clock(), "error": None}
    curve: list[tuple[str, int]] = []
    next_sample = clock()

    def on_tick(tick: Tick) -> None:
        nonlocal next_sample
        platform.mark(tick)
        rt.algos.on_tick(tick)
        now = clock()
        if not state["started"] and now >= start_at:
            state["started"] = True
            try:
                rt.algos.start(spec.algo, user_id="backtest", instrument_id=spec.instrument_id,
                               side=Side(spec.side), qty=spec.qty, params=dict(spec.params),
                               product=Product(spec.product))  # fmt: skip
            except AlgoError as exc:
                state["error"] = exc.reason
        while state["next_beat"] <= now:
            rt.algos.heartbeat()
            state["next_beat"] += BEAT
        if now >= next_sample:
            curve.append((now.isoformat(), rt.positions.day_pnl_paise()))
            next_sample = now + timedelta(minutes=1)

    md.tap(on_tick)

    async def drain(_seconds: float) -> None:
        """Between ticks: let everything a tick started finish (an algo's order through the
        gates, the broker's acceptance, its fills, the order manager's events), so each fill is
        stamped with the time of the tick that caused it, as it would be live."""
        for _ in range(DRAIN_TURNS):
            await asyncio.sleep(0)
            if broker.idle and (not rt.algos.busy or asleep["n"]):  # a wait needs the clock
                return
        log.warning("%s: still busy after %d loop turns", day, DRAIN_TURNS)

    feed = ReplayFeed(lambda: _chain(first, ticks), md, clock, day=day, speed=0, sleep=drain)
    runner = asyncio.create_task(rt.orders.run())
    try:
        await feed.run()
        await rt.algos.settle()
        await broker.settle()
        for _ in range(3):
            await asyncio.sleep(0)
        await events.flush()
        if state["error"]:
            raise BacktestError(f"{day}: the algo couldn't start: {state['error']}")
        report = report_to_dict(build_day(ACCOUNT, day, events, instruments.get, charges,
                                          prices=md.latest, now=clock(), final=True))  # fmt: skip
        curve.append((clock().isoformat(), rt.positions.day_pnl_paise()))
        report["equity_curve"] = curve
        report["algo_started"] = state["started"]
        return report
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await broker.aclose()
        await events.aclose()


def _open(source: DayInput, instrument: Instrument | None) -> Iterator[tuple[int, Tick]]:
    if isinstance(source, RecordedDay):
        return source.open()
    if isinstance(source, CandleDay):
        if instrument is None:  # candles become ticks on the instrument's own tick grid
            raise BacktestError("the instrument isn't in the instrument list: no candle replay")
        return iter(candles_to_ticks(instrument, source.rows))
    return source


class InProcess:
    """Days run on the caller's own loop: tests, and a source that can't be handed over."""

    async def day(self, spec: BacktestSpec, day: date, source: DayInput,
                  instruments: InstrumentStore, charges: ChargesModel,
                  folder: Path) -> dict[str, Any]:  # fmt: skip
        ticks = _open(source, instruments.get(spec.instrument_id))
        return await run_day(spec, day, ticks, instruments, charges, folder)

    def stop(self) -> None:
        """Nothing to stop: cancelling the run's task stops the day."""

    def close(self) -> None:
        pass


def _cap_memory(limit: int) -> None:
    """In the worker, before any day: an address-space cap, so a runaway day fails alone."""
    if limit > 0:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _day_in_worker(spec: BacktestSpec, day: date, source: RecordedDay | CandleDay,
                   instruments: list[Instrument], charges: ChargesModel,
                   folder: Path) -> dict[str, Any]:  # fmt: skip
    store = InstrumentStore()
    for i in instruments:
        store.add(i)
    ticks = _open(source, store.get(spec.instrument_id))
    return asyncio.run(run_day(spec, day, ticks, store, charges, folder))


class Worker:
    """Days run in a process of their own (#274): the trading loop never runs a backtest's
    ticks, reads its files or builds its reports. One process, one day at a time; it is
    started when first needed and stopped (killed) on a cancel, a time-out or close."""

    def __init__(self, *, timeout_s: float = DAY_TIMEOUT_S, memory: int = WORKER_MEMORY) -> None:
        self._timeout_s = timeout_s
        self._memory = memory
        self._pool: concurrent.futures.ProcessPoolExecutor | None = None

    def _executor(self) -> concurrent.futures.ProcessPoolExecutor:
        if self._pool is None:
            self._pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                initializer=_cap_memory, initargs=(self._memory,),
            )  # fmt: skip
        return self._pool

    async def day(self, spec: BacktestSpec, day: date, source: DayInput,
                  instruments: InstrumentStore, charges: ChargesModel,
                  folder: Path) -> dict[str, Any]:  # fmt: skip
        if not isinstance(source, RecordedDay | CandleDay):  # an iterator can't be handed over
            return await InProcess().day(spec, day, source, instruments, charges, folder)
        everything = list(instruments._by_id.values())
        job = asyncio.get_running_loop().run_in_executor(
            self._executor(), _day_in_worker, spec, day, source, everything, charges, folder
        )
        try:
            return await asyncio.wait_for(job, self._timeout_s)
        except TimeoutError:
            self.stop()
            raise BacktestError(
                f"{day}: took longer than {self._timeout_s:.0f} s, stopped"
            ) from None
        except concurrent.futures.process.BrokenProcessPool:
            self.stop()
            raise BacktestError(f"{day}: the backtest worker stopped (out of memory?)") from None
        except MemoryError:
            raise BacktestError(f"{day}: needed more memory than a backtest may use") from None

    def stop(self) -> None:
        """Kill the worker (a cancel or a time-out); the next day starts a fresh one."""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        for process in list((pool._processes or {}).values()):
            process.kill()
        pool.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        self.stop()


def summarise(days: list[dict[str, Any]]) -> dict[str, Any]:
    """The run's figures from its days, in order."""
    ran = [d for d in days if "totals" in d]
    equity, peak, worst = 0, 0, 0
    curve = []
    for d in ran:
        equity += d["totals"]["net_paise"]
        curve.append((d["day"], equity))
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    winning = sum(1 for d in ran if d["totals"]["net_paise"] > 0)
    return {
        "days_run": len(ran),
        "days_skipped": [d["day"] for d in days if "skipped" in d],
        "gross_paise": sum(d["totals"]["gross_paise"] for d in ran),
        "charges_paise": sum(d["totals"]["charges_paise"] for d in ran),
        "net_paise": equity,
        "max_drawdown_paise": -worst,  # the deepest fall of cumulative net from a high
        "winning_days": winning,
        "win_rate": round(winning / len(ran), 4) if ran else None,
        "trades": sum(d["totals"]["trades"] for d in ran),
        "equity_curve": curve,
    }


# --- runs: started, followed, kept ------------------------------------------------------------


@dataclass
class Run:
    run_id: str
    spec: BacktestSpec
    candles: bool
    state: str = "queued"  # queued | running | done | failed | cancelled
    progress: int = 0  # days done
    error: str = ""
    created_at: str = ""
    result: dict[str, Any] | None = None
    owner: str = ""  # who started it (#270); "" (kept from before #270): admins only

    def may_see(self, user_id: str, admin: bool) -> bool:
        """A run is its owner's; an admin sees every run."""
        return admin or self.owner == user_id  # "" is nobody's: user ids are never empty

    def to_dict(self, full: bool = True) -> dict[str, Any]:
        out = {"run_id": self.run_id, "spec": asdict(self.spec), "candle_based": self.candles,
               "state": self.state, "progress": self.progress, "days": len(self.spec.days),
               "error": self.error, "created_at": self.created_at,
               "owner": self.owner}  # fmt: skip
        if self.candles:
            out["fills_note"] = CANDLE_NOTE
        if self.result is not None:
            out["summary"] = self.result["summary"]
            if full:
                out["daily"] = self.result["daily"]
        return out


class Backtests:
    """Runs one at a time in the background; each kept as JSON in `<root>/<run_id>/`."""

    def __init__(
        self,
        root: Path,
        instruments: InstrumentStore,
        charges: ChargesModel,
        ticks: TickSource,
        *,
        candles: CandleSource | None = None,
        sources_of: Callable[[str], tuple[TickSource, CandleSource | None]] | None = None,
        runner: InProcess | Worker | None = None,  # where days run: a Worker on a server
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = Path(root)
        self._instruments = instruments
        self._charges = charges
        # what a run replays, by who started it (#396): their own subscription's recordings
        # and their own firm's candles. Without it, everyone's runs read the same data.
        self._sources = sources_of or (lambda _owner: (ticks, candles))
        self._clock = clock
        self._runner = runner or InProcess()
        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()  # one run at a time: a backtest is CPU-bound
        self._tasks: dict[str, asyncio.Task] = {}  # run id -> its task, while queued or running
        self._load()

    def start(self, spec: BacktestSpec, *, candles: bool = False, owner: str = "") -> Run:
        problems = spec.problems()
        if self._instruments.get(spec.instrument_id) is None:
            problems.append(f"unknown instrument {spec.instrument_id}")
        if spec.algo not in {t.spec.code for t in BUILTIN}:
            problems.append(f"unknown algo {spec.algo!r}")
        if candles and self._sources(owner)[1] is None:
            problems.append("no candles were imported")
        if problems:
            raise BacktestError("; ".join(problems))
        waiting = [self._runs[r] for r in self._tasks]
        if len(waiting) >= MAX_WAITING:
            raise BacktestBusy(f"{len(waiting)} backtests are already waiting: try later")
        if sum(r.owner == owner for r in waiting) >= MAX_PER_USER:
            raise BacktestBusy(f"you already have {MAX_PER_USER} backtests waiting or running: "
                               "wait for one, or cancel it")  # fmt: skip
        run_id = self._new_id()
        run = Run(run_id, spec, candles, created_at=self._clock().isoformat(), owner=owner)
        self._runs[run.run_id] = run
        self._save(run)
        task = asyncio.ensure_future(self._run(run))
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run.run_id, None))
        return run

    def _new_id(self) -> str:
        """BT + the time to the second + 4 random hex characters, picked again while taken: ten
        runs started in the same second must not share one (#458)."""
        while True:
            run_id = f"BT{self._clock().strftime('%y%m%d%H%M%S')}{secrets.token_hex(2).upper()}"
            if run_id not in self._runs:
                return run_id

    async def cancel(self, run_id: str) -> bool:
        """Stop a queued or running run (its worker is killed) and wait until it has; False
        if it had already ended."""
        task = self._tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        return True

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def day(self, run_id: str, day: str) -> dict[str, Any]:
        """One day of a finished run: the K10.3 report (for CSV/PDF) or why there is none."""
        run = self._runs.get(run_id)
        if run is None:
            raise LookupError(f"no backtest {run_id}")
        if run.result is None:
            raise LookupError(f"backtest {run_id} hasn't finished ({run.state})")
        for d in run.result["daily"]:
            if d["day"] == day:
                if "skipped" in d:
                    raise LookupError(f"{day} was skipped: {d['skipped']}")
                return d
        raise LookupError(f"{day} isn't one of the days of {run_id}")

    def runs(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    async def settle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    def close(self) -> None:
        """The server is stopping: its worker goes too. A run cut short reads as failed on the
        next start ("the server stopped during the run"), not as cancelled."""
        self._runner.close()

    async def _run(self, run: Run) -> None:
        try:
            async with self._lock:
                await self._days(run)
        except asyncio.CancelledError:  # cancelled while queued or running (#274)
            if run.state == "running":  # only its own day: a queued one stops nobody's
                self._runner.stop()
            run.state, run.error = "cancelled", "cancelled"
        self._save(run)

    async def _days(self, run: Run) -> None:
        run.state = "running"
        self._save(run)
        daily = []
        try:
            for text in run.spec.days:
                day = date.fromisoformat(text)
                source = self._source(run, day)
                if source is None:
                    daily.append({"day": text, "skipped": "nothing recorded"})
                else:
                    folder = self.root / run.run_id
                    daily.append(await self._runner.day(run.spec, day, source, self._instruments,
                                                        self._charges, folder))  # fmt: skip
                run.progress += 1
                self._save(run)
            run.result = {"summary": summarise(daily), "daily": daily}
            run.state = "done"
        except BacktestError as exc:
            run.state, run.error = "failed", str(exc)
        except Exception as exc:
            log.exception("backtest %s failed", run.run_id)
            run.state, run.error = "failed", f"{type(exc).__name__}: {exc}"

    def _source(self, run: Run, day: date) -> DayInput | None:
        ticks, candles = self._sources(run.owner)
        if run.candles:
            rows = candles(run.spec.instrument_id, day) if candles else None
            return CandleDay(tuple(rows)) if rows else None
        return ticks(day)

    def _save(self, run: Run) -> None:
        folder = self.root / run.run_id
        folder.mkdir(parents=True, exist_ok=True)
        data = run.to_dict()
        tmp = folder / "run.tmp"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, folder / "run.json")

    def _load(self) -> None:
        if not self.root.is_dir():
            return
        for path in self.root.glob("*/run.json"):
            try:
                d = json.loads(path.read_text())
                spec = BacktestSpec(**{**d["spec"], "days": tuple(d["spec"]["days"])})
                run = Run(d["run_id"], spec, d["candle_based"], d["state"], d["progress"],
                          d["error"], d["created_at"], owner=d.get("owner", ""))  # fmt: skip
                if d.get("summary") is not None:
                    run.result = {"summary": d["summary"], "daily": d.get("daily", [])}
                if run.state in ("queued", "running"):  # the server stopped mid-run
                    run.state, run.error = "failed", "the server stopped during the run"
                self._runs[run.run_id] = run
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("backtest %s can't be read: %s", path, exc)


def form_spec(ticks_days: list[date], *, candles: bool) -> dict[str, Any]:
    """What a backtest screen offers (drawn by the screen, not hard-coded there): the algos
    with their settings, the recorded days, the products and the defaults."""
    d = BacktestSpec(days=(), algo="", instrument_id="", side="BUY", qty=1)
    return {
        "algos": [spec_to_dict(t.spec) for t in BUILTIN],
        "days": [x.isoformat() for x in reversed(ticks_days)],  # newest first
        "products": [p.value for p in Product],
        "candles": candles,  # candle-based runs are possible (imported candles)
        "defaults": {"product": d.product, "start": d.start, "capital_paise": d.capital_paise},
        "max_days": MAX_DAYS,
    }


def _chain(first, rest):
    yield first
    yield from rest


# an instrument id as the store accepts it: EXCHANGE:SYMBOL, no path characters (#269)
_CANDLE_ID = re.compile(r"^[A-Z]{2,4}:[A-Za-z0-9][A-Za-z0-9 &._-]{0,59}$")
_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CandleStore:
    """Imported OHLC candles: `<root>/<instrument id, ':' as '_'>/<YYYY-MM-DD>.json`.

    The instrument id becomes a folder name, so it is checked first: EXCHANGE:SYMBOL with no
    path characters, and the folder must sit directly under the root (a `..` or an absolute
    path can never reach outside it). A day is written whole or not at all."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _folder(self, instrument_id: str) -> Path:
        if not _CANDLE_ID.match(instrument_id) or ".." in instrument_id:
            raise BacktestError(f"not an instrument id: {instrument_id!r}")
        folder = self.root / instrument_id.replace(":", "_")
        if folder.resolve().parent != self.root.resolve():
            raise BacktestError(f"not an instrument id: {instrument_id!r}")  # never outside
        return folder

    def put(self, instrument_id: str, day: date, rows: list[dict[str, Any]]) -> int:
        folder = self._folder(instrument_id)
        problems = []
        seen: set[datetime] = set()
        for n, r in enumerate(rows, 1):
            try:
                o, h, low, c = (_whole(r[k]) for k in ("open", "high", "low", "close"))
                volume = _whole(r.get("volume", 0))
                at = datetime.fromisoformat(r["time"])
            except (KeyError, ValueError, TypeError):
                problems.append(f"row {n}: needs time (ISO), open, high, low, close (paise) and "
                                "an optional whole volume")  # fmt: skip
                continue
            if at.tzinfo is None or at.astimezone(IST).date() != day:
                problems.append(f"row {n}: the time must carry its zone and fall on {day}")
            elif at in seen:
                problems.append(f"row {n}: a second candle at {r['time']}")
            seen.add(at)
            if not (low <= min(o, c) and max(o, c) <= h and low > 0):
                problems.append(f"row {n}: low ≤ open, close ≤ high, and prices above 0")
            if volume < 0:
                problems.append(f"row {n}: volume can't be negative")
            extra = set(r) - {"time", "open", "high", "low", "close", "volume"}
            if extra:
                problems.append(f"row {n}: unknown field {', '.join(sorted(extra))}")
        if problems:
            raise BacktestError("; ".join(problems[:10]))
        folder.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: datetime.fromisoformat(r["time"]))
        target = folder / f"{day.isoformat()}.json"
        part = target.with_suffix(".part")
        part.write_text(json.dumps(rows))
        os.replace(part, target)  # whole or nothing: a reader never sees half a day
        return len(rows)

    def days(self, instrument_id: str) -> list[str]:
        """The days with candles for an instrument, newest first (none for an id the store
        can't hold: nothing could have been imported for it)."""
        try:
            folder = self._folder(instrument_id)
        except BacktestError:
            return []
        return sorted((p.stem for p in folder.glob("*.json") if _DAY_FILE.match(p.stem)),
                      reverse=True)  # fmt: skip

    def rows(self, instrument_id: str, day: date) -> list[dict[str, Any]] | None:
        try:
            path = self._folder(instrument_id) / f"{day.isoformat()}.json"
        except BacktestError:
            return None
        return json.loads(path.read_text()) if path.exists() else None


class CandleStores:
    """Imported candles kept per workspace (#396): the main workspace's in `<root>`, as before
    #368; another firm's in `<root>/@<workspace>`. A firm's imports feed only its own
    backtests, and its admin imports without touching another firm's. ("@" never starts an
    instrument's folder, so the two can't meet.)"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def of(self, workspace: str) -> CandleStore:
        if workspace == MAIN:
            return CandleStore(self.root)
        if not workspace.isascii() or not workspace.replace("-", "").isalnum():
            raise BacktestError(f"not a workspace id: {workspace!r}")  # never a path
        return CandleStore(self.root / f"@{workspace}")


def _whole(value: Any) -> int:
    """A whole number of paise (or a volume); `True`, 1.5 and "12" are refused."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("not a whole number")
    return value
