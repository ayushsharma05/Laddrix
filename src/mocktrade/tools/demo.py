"""Run the whole platform locally with simulated prices and paper trading: no Kite needed.

    python -m mocktrade.tools.demo                  # http://localhost:8000
    python -m mocktrade.tools.demo --code           # today's 6-digit login code
    python -m mocktrade.tools.demo --origin http://localhost:5173   # with the UI dev server
    python -m mocktrade.tools.demo --replay 2026-10-16 --speed 10   # a recorded day (K11.2)
    python -m mocktrade.tools.demo --replay 2026-10-16 --code       # its login code

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

from mocktrade.algos.builtin import BUILTIN
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sim_universe import INSTRUMENTS
from mocktrade.marketdata.simulator import MarketSimulator
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.guards import DayGuards
from mocktrade.ops.scheduler import Scheduler
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.reports.service import Reports
from mocktrade.reports.store import ReportStore
from mocktrade.research.backtest import Backtests, CandleStore
from mocktrade.research.replay import EPOCH, SPEEDS, ReplayClock, ReplayFeed
from mocktrade.research.ticks import IST, FileTickStore, TickRecorder
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.server import security
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog

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
    users = setup_users(data)
    instruments = InstrumentStore()
    for instrument, _ in INSTRUMENTS:
        instruments.add(instrument)
    md = MarketData()
    feed = None
    if replay_day is None:
        simulator = MarketSimulator(md, {i: p for i, p in INSTRUMENTS})
        simulator.step()  # every instrument has a price before the first order
        clock = lambda: datetime.now(UTC)  # noqa: E731
        market_job = simulator.run()
    else:
        recorded = FileTickStore(ticks_dir)
        if replay_day not in recorded.days():
            raise SystemExit(f"nothing recorded on {replay_day} in {ticks_dir}")
        first_us, _ = next(recorded.read_timed(replay_day))
        clock = ReplayClock(EPOCH + timedelta(microseconds=first_us))
        feed = ReplayFeed(lambda: recorded.read_timed(replay_day), md, clock,
                          day=replay_day, speed=speed)  # fmt: skip
        market_job = feed.run()
    log = EventLog(data / "events.db")
    log.start()
    platform = Platform(instruments, md, KillSwitch("firm"), log)
    brokers = []
    for account in users.accounts():
        broker = PaperBroker(md, instruments, name="paper", clock=clock,
                             every_tick=feed is not None)  # fmt: skip
        broker.start()
        brokers.append(broker)
        day = clock().astimezone(IST).strftime("%y%m%d")
        ids = ClientIds(f"D{day}", log.last_counter(f"D{day}"))
        platform.add_account(
            account, broker, ids=ids, auto_kill_on_loss=True, algo_types=BUILTIN,
            recover_on_start=True, margin_check=True, rules_dir=data / "rules", clock=clock,
        )  # fmt: skip
    settings = ServerSettings(origin=origin, cookie_secure=False)
    reports = Reports(platform, ReportStore(data / "reports"), clock=clock)
    scheduler = Scheduler(clock=clock)
    scheduler.daily("16:00", reports.close_of_day, "end-of-day reports")
    alerts = Alerts(clock=clock)
    jobs = [market_job, platform.run(), scheduler.run()]
    ticks = None
    if feed is None:  # a replay doesn't record what it replays
        ticks = FileTickStore(data / "ticks", keep_days=7)
        recorder = TickRecorder(ticks)
        md.tap(recorder.on_tick)
        jobs.append(recorder.run())
    else:
        feed.subscribe(lambda status: _replay_band(alerts, status))
        _replay_band(alerts, feed.status())
    recorded_ticks = FileTickStore(data / "ticks" if feed is None else ticks_dir)
    candle_store = CandleStore(data / "candles")
    backtests = Backtests(
        data / "backtests", instruments, platform.charges_model,
        ticks=lambda d: recorded_ticks.read_timed(d) if d in recorded_ticks.days() else None,
        candles=candle_store.rows,
    )  # fmt: skip
    guards = DayGuards(platform, alerts)
    scheduler.every(60, guards.check, between=("09:15", "15:35"), name="day guards")
    app = create_app(platform, users, settings, static_dir=ui, scheduler=scheduler,
                     reports=reports, alerts=alerts, ticks=ticks, replay=feed,
                     replay_command=lambda d: f"python -m mocktrade.tools.demo --replay {d} "
                                              f"--port {port + 1}",
                     backtests=backtests, candles=candle_store)  # fmt: skip
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    what = "demo" if feed is None else f"REPLAY of {replay_day} at {speed:g}×"
    code = "python -m mocktrade.tools.demo --code" + (f" --replay {replay_day}" if feed else "")
    print(f"\nMockTrade {what} on http://localhost:{port}  (pages from {origin})")
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


def _replay_band(alerts: Alerts, status: dict) -> None:
    """A band on every screen: this is a replay, not the market."""
    speed = "max speed" if not status["speed"] else f"{status['speed']:g}×"
    alerts.raise_("replay", "WARN", f"REPLAY of {status['day']} ({status['state']}, {speed}): "
                  "practice on a recorded day; nothing reaches the market")  # fmt: skip


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="mocktrade.tools.demo")
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
