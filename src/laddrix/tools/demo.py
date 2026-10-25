"""Run the whole platform locally with simulated prices and paper trading: no Kite needed.

    python -m laddrix.tools.demo                  # http://localhost:8000
    python -m laddrix.tools.demo --code           # today's 6-digit login code
    python -m laddrix.tools.demo --origin http://localhost:5173   # with the UI dev server
    python -m laddrix.tools.demo --replay 2026-10-16 --speed 10   # a recorded day (K11.2)
    python -m laddrix.tools.demo --replay 2026-10-16 --code       # its login code

Everything lives in data/demo/ (git-ignored): a demo user, a paper account, a throwaway
secret key. Prices come from the market simulator; the paper broker fills against them
exactly as it would against Kite. For development and showing the screens only.

With --replay, prices come from a recorded day instead (research/replay.py), and everything
lives in data/replay/<day>/: its own user, paper account, audit trail and reports, so practice
never mixes with anything else. Every part runs on the replayed time; a REPLAY band says so.
"""

import argparse
import asyncio
import contextlib
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import uvicorn

from laddrix import logsafe
from laddrix.alerts.service import build_market_alerts
from laddrix.algos.autospreader import algo_type as ase_type
from laddrix.algos.builtin import BUILTIN
from laddrix.broker.paper import PaperBroker
from laddrix.charts.candles import CandleBuilder
from laddrix.charts.history import RecordedHistory
from laddrix.charts.service import Charts
from laddrix.domain.accounts import BrokerAccount, Role
from laddrix.marketdata.service import MarketData
from laddrix.marketdata.sim_universe import FREEZE, INSTRUMENTS
from laddrix.marketdata.simulator import MarketSimulator
from laddrix.ops.alerts import Alerts
from laddrix.ops.checks import PriceOverload, audit_log
from laddrix.ops.fence import DataFence, FenceError
from laddrix.ops.guards import DayGuards
from laddrix.ops.protection import ProtectionGuard
from laddrix.ops.scheduler import Scheduler
from laddrix.orders.model import ClientIds
from laddrix.platform import Platform
from laddrix.refdata.store import InstrumentStore
from laddrix.reports.service import Reports
from laddrix.reports.store import ReportStore
from laddrix.research.backtest import Backtests, CandleStores, RecordedDay, Worker
from laddrix.research.replay import (
    EPOCH,
    SPEEDS,
    ReplayClock,
    ReplayFeed,
    ReplayInstrumentsError,
    replay_instruments,
)
from laddrix.research.ticks import IST, FileTickStore, TickRecorder
from laddrix.risk.kill_switch import KillSwitch
from laddrix.risk.limits_spec import stored_limits
from laddrix.server import security
from laddrix.server.admin import CallbackStarter
from laddrix.server.app import ServerSettings, create_app
from laddrix.server.security import SecretBox
from laddrix.server.users import UserStore
from laddrix.spreads.store import SpreadBook
from laddrix.store.events import EventLog

DATA = Path("data/demo")
REPLAYS = Path("data/replay")  # one folder per replayed day: practice kept apart
USER, PASSWORD = "demo", "demo-password-123"


def secret_key(data: Path = DATA) -> str:
    path = data / "secret.key"
    if not path.exists():
        data.mkdir(parents=True, exist_ok=True)
        path.write_text(SecretBox.new_key())
        path.chmod(0o600)
    return path.read_text().strip()


def demo_totp_secret(data: Path = DATA) -> str:
    return (data / "demo_totp.txt").read_text().strip()


def setup_users(data: Path = DATA) -> UserStore:
    store = UserStore(data / "users.db", SecretBox(secret_key(data)))
    if store.get_user(USER) is None:
        created = store.create_user(USER, "Demo Trader", PASSWORD, {Role.ADMIN, Role.TRADER})
        secret = parse_qs(urlparse(created.totp_uri).query)["secret"][0]
        (data / "demo_totp.txt").write_text(secret)  # demo only: real secrets are never kept
        store.add_account(BrokerAccount("acc-paper", "paper", "PAPER1", USER))
    return store


async def main(
    port: int,
    origin: str,
    ui: Path | None,
    *,
    replay_day: date | None = None,
    ticks_dir: Path = DATA / "ticks",
    speed: float = 10,
) -> None:
    data = DATA if replay_day is None else REPLAYS / replay_day.isoformat()
    instruments = InstrumentStore()
    if replay_day is None:
        for instrument, _ in INSTRUMENTS:
            instruments.add(instrument)
        instruments.set_freeze(FREEZE)  # sample freeze quantities (#424)
    else:  # the day's own instruments, as they were that day (an old demo day: the demo's)
        recorded = FileTickStore(ticks_dir)
        if replay_day not in recorded.days():
            raise SystemExit(f"nothing recorded on {replay_day} in {ticks_dir}")
        try:
            day_instruments = replay_instruments(recorded, replay_day, [i for i, _ in INSTRUMENTS])
        except ReplayInstrumentsError as exc:
            raise SystemExit(f"can't replay: {exc}") from None
        for instrument in day_instruments:
            instruments.add(instrument)
    fence = DataFence(data)  # one server per data folder, like the real one (#273)
    try:
        fence.acquire()
    except FenceError as exc:
        raise SystemExit(str(exc)) from None
    users = setup_users(data)  # after the checks: a refused replay leaves nothing behind
    md = MarketData()
    feed = None
    if replay_day is None:
        simulator = MarketSimulator(md, {i: p for i, p in INSTRUMENTS})
        simulator.step()  # every instrument has a price before the first order
        clock = lambda: datetime.now(UTC)  # noqa: E731
        market_job = simulator.run()
    else:
        first_us, _ = next(recorded.read_timed(replay_day))
        clock = ReplayClock(EPOCH + timedelta(microseconds=first_us))
        feed = ReplayFeed(lambda: recorded.read_timed(replay_day), md, clock,
                          day=replay_day, speed=speed)  # fmt: skip
        market_job = feed.run()
    log = EventLog(data / "events.db")
    log.start()
    platform = Platform(instruments, md, KillSwitch("firm"), log)
    # each user's spreads (#414): the screens price them, the ASE algo trades them (#415)
    spreads = SpreadBook.at(data / "spreads", instruments.get, platform.expiries.expired)
    brokers = []
    day = clock().astimezone(IST).strftime("%y%m%d")
    ids = ClientIds(f"D{day}", log.last_counter(f"D{day}"))  # one sequence, all accounts

    def start(account: BrokerAccount, limits: dict | None) -> str | None:
        """Run one paper account (at start, or an admin's new one)."""
        broker = PaperBroker(md, instruments, name="paper", clock=clock,
                             every_tick=feed is not None)  # fmt: skip
        broker.start()
        brokers.append(broker)
        platform.add_account(
            account, broker, ids=ids, auto_kill_on_loss=True,
            algo_types=(*BUILTIN, ase_type(spreads.get)),
            recover_on_start=True, margin_check=True, rules_dir=data / "rules", clock=clock,
            limits=stored_limits(limits),
        )  # fmt: skip
        return None

    for account in users.accounts():
        start(account, users.account_limits(account.id))
    settings = ServerSettings(origin=origin, cookie_secure=False)
    reports = Reports(platform, ReportStore(data / "reports"), clock=clock)
    scheduler = Scheduler(clock=clock)
    scheduler.daily("08:45", platform.roll_day, "new trading day")  # #266: no restart needed
    scheduler.daily("16:00", reports.close_of_day, "end-of-day reports")
    alerts = Alerts(clock=clock)
    jobs = [market_job, platform.run(), scheduler.run()]
    ticks = None
    if feed is None:  # a replay doesn't record what it replays
        ticks = FileTickStore(data / "ticks", keep_days=7, describe=instruments.get)
        recorder = TickRecorder(ticks)
        md.tap(recorder.on_tick)
        jobs.append(recorder.run())
    else:
        feed.subscribe(lambda status: _replay_band(alerts, status))
        _replay_band(alerts, feed.status())
    recorded_ticks = FileTickStore(data / "ticks" if feed is None else ticks_dir)
    candle_stores = CandleStores(data / "candles")  # each firm imports its own (#396)

    def recorded_day(d):  # opened by the backtest worker, never on the trading loop (#274)
        return RecordedDay(recorded_ticks.root, d) if d in recorded_ticks.days() else None

    def sources_of(owner: str):
        """A backtest replays the simulator's days (everyone's) or its owner's firm's candles."""
        user = users.get_user(owner)
        return recorded_day, candle_stores.of(user.workspace).rows if user is not None else None

    backtests = Backtests(data / "backtests", instruments, platform.charges_model,
                          ticks=recorded_day, sources_of=sources_of, runner=Worker())  # fmt: skip
    builder = CandleBuilder(clock=clock)  # charts: candles from every tick, history recorded
    md.tap(builder.on_tick)
    charts = Charts(builder, RecordedHistory(recorded_ticks, clock=clock), events=log,
                    clock=clock, history_name="recorded ticks")  # fmt: skip
    market_alerts = build_market_alerts(platform, data / "alerts", clock)  # each user's own
    jobs.append(market_alerts.store.run())
    guards = DayGuards(platform, alerts)
    scheduler.every(60, guards.check, between=("09:15", "15:35"), name="day guards")
    scheduler.every(
        60, guards.check_mtm_job, between=("00:00", "23:59"), name="mtm closes", any_day=True
    )
    protection = ProtectionGuard(platform, alerts)  # lost stops on open positions (V1.2)
    protection.after_restart()
    scheduler.every(
        5, protection.run_check, between=("00:00", "23:59"), name="protection guard", any_day=True
    )
    scheduler.every(5, lambda: audit_log(alerts, log), between=("00:00", "23:59"),
                    name="audit log", any_day=True)  # fmt: skip
    overload = PriceOverload(alerts, md)
    scheduler.every(15, overload.check, between=("00:00", "23:59"), name="price overload",
                    any_day=True)  # fmt: skip
    app = create_app(platform, users, settings, static_dir=ui, scheduler=scheduler,
                     reports=reports, alerts=alerts, ticks=ticks, replay=feed,
                     protection=protection,
                     replay_command=lambda d: f"python -m laddrix.tools.demo --replay {d} "
                                              f"--port {port + 1}",
                     backtests=backtests, candles_for=candle_stores.of, charts=charts,
                     market_alerts=market_alerts,
                     spreads=spreads,
                     starter=CallbackStarter(("paper",), start))  # fmt: skip
    jobs.append(app.state.status.watch())  # "since when" on the status page (#287)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    logsafe.install()  # the same filter as the real server: no secret in a log line (#367)
    what = "demo" if feed is None else f"REPLAY of {replay_day} at {speed:g}×"
    code = "python -m laddrix.tools.demo --code" + (f" --replay {replay_day}" if feed else "")
    print(f"\nLaddrix {what} on http://localhost:{port}  (pages from {origin})")
    print(f"user: {USER}   password: {PASSWORD}   code: {code}\n")
    background = [asyncio.create_task(job) for job in jobs]
    try:
        await server.serve()
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for broker in brokers:
            await broker.aclose()
        await log.aclose()
        fence.release()


def _replay_band(alerts: Alerts, status: dict) -> None:
    """A band on every screen: this is a replay, not the market."""
    speed = "max speed" if not status["speed"] else f"{status['speed']:g}×"
    alerts.raise_("replay", "WARN", f"REPLAY of {status['day']} ({status['state']}, {speed}): "
                  "practice on a recorded day; nothing reaches the market")  # fmt: skip


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="laddrix.tools.demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--origin", default=None, help="where the pages are served from")
    parser.add_argument("--ui", type=Path, default=Path("ui/dist"))
    parser.add_argument("--code", action="store_true", help="print the current login code")
    parser.add_argument("--replay", type=date.fromisoformat, help="a recorded day: YYYY-MM-DD")
    parser.add_argument("--ticks", type=Path, default=DATA / "ticks", help="where it was recorded")
    parser.add_argument("--speed", type=float, default=10, choices=SPEEDS)
    args = parser.parse_args()
    data = DATA if args.replay is None else REPLAYS / args.replay.isoformat()
    if args.code:
        setup_users(data)
        print(security.totp_code(demo_totp_secret(data), time.time()))
    else:
        ui = args.ui if (args.ui / "index.html").exists() else None
        origin = args.origin or f"http://localhost:{args.port}"
        asyncio.run(main(args.port, origin, ui, replay_day=args.replay, ticks_dir=args.ticks,
                         speed=args.speed))  # fmt: skip
