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

Candles (OHLC rows) become ticks O → the far extreme first → the other extreme → C, each with
one tick of spread on either side, so market orders pay the spread and resting orders fill
only when the price trades through them: conservative, and the run says it is candle-based.
"""

import asyncio
import json
import logging
import os
import secrets
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import HEARTBEAT_S, AlgoError
from mocktrade.broker.paper import PaperBroker
from mocktrade.charges.model import ChargesModel
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.reports.daily import build_day, report_to_dict
from mocktrade.research.replay import EPOCH, ReplayClock, ReplayFeed
from mocktrade.research.ticks import IST
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.store.events import EventLog

log = logging.getLogger(__name__)
ACCOUNT = "backtest"
BEAT = timedelta(seconds=HEARTBEAT_S)
DRAIN_TURNS = 200  # far more than one order's path needs (about a dozen)


class BacktestError(Exception):
    """A spec that can't run (said in words)."""


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


TickSource = Callable[[date], Iterator[tuple[int, Tick]] | None]  # None: nothing that day


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
    platform = Platform(instruments, md, KillSwitch("firm"), events, charges_model=charges)
    broker = PaperBroker(md, instruments, latency_s=0.0, clock=clock, every_tick=True,
                         starting_paise=spec.capital_paise)  # fmt: skip
    rt = platform.add_account(
        BrokerAccount(ACCOUNT, "paper", "BT", "backtest"), broker, ids=ClientIds("BT"),
        algo_types=BUILTIN, clock=clock,
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
            if broker.idle and not rt.algos.busy:
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
    state: str = "queued"  # queued | running | done | failed
    progress: int = 0  # days done
    error: str = ""
    created_at: str = ""
    result: dict[str, Any] | None = None

    def to_dict(self, full: bool = True) -> dict[str, Any]:
        out = {"run_id": self.run_id, "spec": asdict(self.spec), "candle_based": self.candles,
               "state": self.state, "progress": self.progress, "days": len(self.spec.days),
               "error": self.error, "created_at": self.created_at}  # fmt: skip
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
        candles: Callable[[str, date], list[dict[str, Any]] | None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = Path(root)
        self._instruments = instruments
        self._charges = charges
        self._ticks = ticks
        self._candles = candles
        self._clock = clock
        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()  # one run at a time: a backtest is CPU-bound
        self._tasks: set[asyncio.Task] = set()
        self._load()

    def start(self, spec: BacktestSpec, *, candles: bool = False) -> Run:
        problems = spec.problems()
        if self._instruments.get(spec.instrument_id) is None:
            problems.append(f"unknown instrument {spec.instrument_id}")
        if candles and self._candles is None:
            problems.append("no candles were imported")
        if problems:
            raise BacktestError("; ".join(problems))
        run = Run(f"BT{self._clock().strftime('%y%m%d%H%M%S')}{secrets.token_hex(2).upper()}",
                  spec, candles, created_at=self._clock().isoformat())  # fmt: skip
        self._runs[run.run_id] = run
        self._save(run)
        task = asyncio.ensure_future(self._run(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def runs(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    async def settle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self, run: Run) -> None:
        async with self._lock:
            run.state = "running"
            self._save(run)
            daily = []
            try:
                instrument = self._instruments.get(run.spec.instrument_id)
                for text in run.spec.days:
                    day = date.fromisoformat(text)
                    ticks = self._source(run, instrument, day)
                    if ticks is None:
                        daily.append({"day": text, "skipped": "nothing recorded"})
                    else:
                        folder = self.root / run.run_id
                        daily.append(await run_day(run.spec, day, ticks, self._instruments,
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
            self._save(run)

    def _source(self, run: Run, instrument, day: date) -> Iterator[tuple[int, Tick]] | None:
        if run.candles:
            rows = self._candles(run.spec.instrument_id, day) if self._candles else None
            return iter(candles_to_ticks(instrument, rows)) if rows else None
        return self._ticks(day)

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
                          d["error"], d["created_at"])  # fmt: skip
                if d.get("summary") is not None:
                    run.result = {"summary": d["summary"], "daily": d.get("daily", [])}
                if run.state in ("queued", "running"):  # the server stopped mid-run
                    run.state, run.error = "failed", "the server stopped during the run"
                self._runs[run.run_id] = run
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("backtest %s can't be read: %s", path, exc)


def _chain(first, rest):
    yield first
    yield from rest


class CandleStore:
    """Imported OHLC candles: `<root>/<instrument id, ':' as '_'>/<YYYY-MM-DD>.json`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def put(self, instrument_id: str, day: date, rows: list[dict[str, Any]]) -> int:
        problems = []
        for n, r in enumerate(rows, 1):
            try:
                o, h, low, c = (int(r[k]) for k in ("open", "high", "low", "close"))
                at = datetime.fromisoformat(r["time"])
            except (KeyError, ValueError, TypeError):
                problems.append(f"row {n}: needs time (ISO), open, high, low, close (paise)")
                continue
            if at.tzinfo is None or at.astimezone(IST).date() != day:
                problems.append(f"row {n}: the time must carry its zone and fall on {day}")
            if not (low <= min(o, c) and max(o, c) <= h and low > 0):
                problems.append(f"row {n}: low ≤ open, close ≤ high, and prices above 0")
        if problems:
            raise BacktestError("; ".join(problems[:10]))
        folder = self.root / instrument_id.replace(":", "_")
        folder.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: datetime.fromisoformat(r["time"]))
        (folder / f"{day.isoformat()}.json").write_text(json.dumps(rows))
        return len(rows)

    def rows(self, instrument_id: str, day: date) -> list[dict[str, Any]] | None:
        path = self.root / instrument_id.replace(":", "_") / f"{day.isoformat()}.json"
        return json.loads(path.read_text()) if path.exists() else None
