"""Run MockTrade as a server: the entry point the Docker image starts.

    python -m mocktrade.tools.serve            # settings from the environment (or .env)
    python -m mocktrade.tools.serve --check    # check the settings and exit

Settings (environment; secrets never on the command line):

    MOCKTRADE_SECRET_KEY       required   encrypts secrets at rest (users tool: new-key)
    MOCKTRADE_ORIGIN           required   where the screens are served from (https://host)
    MOCKTRADE_DATA             data       users.db, events.db, broker tokens (a Docker volume)
    MOCKTRADE_HOST             127.0.0.1  0.0.0.0 inside a container
    MOCKTRADE_PORT             8000
    MOCKTRADE_UI               ui/dist    the built screens
    MOCKTRADE_PRICES           sim        sim: simulated prices, paper accounts only
                                          kite: live Kite prices; kite accounts trade for real
                                          after their daily login, paper accounts fill
                                          against live prices
    MOCKTRADE_HOLIDAYS         (none)     exchange holidays, e.g. 2026-10-21,2026-11-10
                                          (no morning reminders or price alerts on them)
    MOCKTRADE_PAPER_FUNDS      1000000    each paper account's starting balance, rupees
    MOCKTRADE_RECORD_DAYS      30         keep this many days of recorded ticks (research,
                                          replay, backtests); 0 turns recording off
    MOCKTRADE_CHARGES          (built in) a charge schedule (JSON, charges/schedules.py) to use
                                          instead of the built-in rates, e.g. after a budget
    MOCKTRADE_ALGO_IDS         (none)     exchange algo ids, once an algo is registered
                                          (SEBI, above 10 orders a second): SLICER=AB123,...
    MOCKTRADE_ALERT_WEBHOOK    (none)     https:// address that gets WARN/ERROR alerts as
                                          JSON {"text": ...} (Slack, ntfy, Google Chat...)
    KITE_API_KEY, KITE_API_SECRET         the Kite Connect app (kite mode). One account with
                                          its own app: KITE_API_KEY_<ACCOUNT>, e.g.
                                          KITE_API_KEY_ACC_AYUSH (and ..._SECRET_...)
    MOCKTRADE_TRUSTED_PROXIES  (none)     the reverse proxy's addresses: only its
                                          X-Forwarded-For is believed, so login limits
                                          see the real client

In Kite's developer console, the app's redirect URL is <MOCKTRADE_ORIGIN>/kite/callback.

This file only assembles parts (users, prices, brokers, platform, web server); each part is
replaceable on its own. Accounts are started by their broker: "paper" accounts trade against
the prices in use; accounts for a broker this mode can't run are listed as not running, never
silently traded on something else.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI

from mocktrade.alerts.service import build_market_alerts
from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import parse_algo_ids, with_exchange_ids
from mocktrade.broker.kite.http import BASE_URL
from mocktrade.broker.kite.instruments import BROKER as KITE
from mocktrade.broker.kite.live import KiteLive
from mocktrade.broker.kite.ticker import WS_URL
from mocktrade.broker.paper import PaperBroker
from mocktrade.charges.model import ScheduleCharges
from mocktrade.charges.schedules import load_schedule
from mocktrade.charts.candles import CandleBuilder
from mocktrade.charts.history import KiteHistory, RecordedHistory
from mocktrade.charts.service import Charts
from mocktrade.config import ConfigError, KiteSettings, environment
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sim_universe import INSTRUMENTS
from mocktrade.marketdata.simulator import MarketSimulator
from mocktrade.metrics import freeze_heap, watch_loop_lag
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.checks import Checks, PriceOverload, audit_log
from mocktrade.ops.guards import DayGuards
from mocktrade.ops.notify import WebhookNotifier
from mocktrade.ops.protection import ProtectionGuard
from mocktrade.ops.scheduler import Scheduler, parse_holidays
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.reports.service import Reports
from mocktrade.reports.store import ReportStore
from mocktrade.research.backtest import Backtests, CandleStore
from mocktrade.research.ticks import FileTickStore, TickRecorder
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits_spec import stored_limits
from mocktrade.server.admin import CallbackStarter
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog

log = logging.getLogger("mocktrade.serve")

PRICE_SOURCES = ("sim", "kite")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class ServeSettings:
    secret_key: str
    origin: str
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    ui_dir: Path = Path("ui/dist")
    prices: str = "sim"
    trusted_proxies: str = ""
    holidays: frozenset = frozenset()  # exchange holidays (no morning jobs or alerts)
    alert_webhook: str = ""  # alerts to a phone: Slack, ntfy, Google Chat... (JSON POST)
    kite_env: tuple[tuple[str, str], ...] = ()  # the KITE_* settings (kite mode)
    paper_funds_paise: int = 10_00_000_00  # each paper account's starting balance
    charges_file: Path | None = None  # a charge schedule (JSON) instead of the built-in one
    record_days: int = 30  # keep this many days of recorded ticks (0: don't record)
    algo_ids: tuple[tuple[str, str], ...] = ()  # exchange algo ids per algo code (#291)

    def kite_url(self, name: str, default: str) -> str:
        """KITE_API_URL / KITE_WS_URL: only to point at a Kite simulator (load tests)."""
        return dict(self.kite_env).get(name, default)

    def kite_app(self, account: BrokerAccount) -> KiteSettings:
        """The account's own Kite app if it has one, else the shared one."""
        env = dict(self.kite_env)
        suffix = "_" + "".join(c if c.isalnum() else "_" for c in account.id.upper())
        key = env.get(f"KITE_API_KEY{suffix}") or env.get("KITE_API_KEY")
        secret = env.get(f"KITE_API_SECRET{suffix}") or env.get("KITE_API_SECRET")
        if not key or not secret:
            raise ConfigError(
                f"account {account.id}: no Kite app (set KITE_API_KEY and KITE_API_SECRET, "
                f"or KITE_API_KEY{suffix} and KITE_API_SECRET{suffix})"
            )
        return KiteSettings(key, secret, self.data_dir / f"kite_token_{account.id}.json")

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies always, except plain http on this machine (development)."""
        return urlparse(self.origin).scheme == "https"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ServeSettings":
        """Every problem at once, each naming its setting."""
        problems: list[str] = []
        key = env.get("MOCKTRADE_SECRET_KEY", "")
        if not key:
            problems.append("MOCKTRADE_SECRET_KEY is missing (make one: users tool new-key)")
        else:
            try:
                SecretBox(key)
            except Exception:
                problems.append("MOCKTRADE_SECRET_KEY is not a valid key (users tool new-key)")
        origin = env.get("MOCKTRADE_ORIGIN", "").rstrip("/")
        url = urlparse(origin)
        if not origin:
            problems.append("MOCKTRADE_ORIGIN is missing (e.g. https://trade.example.in)")
        elif url.scheme not in ("http", "https") or not url.hostname or url.path:
            problems.append(f"MOCKTRADE_ORIGIN must be scheme://host[:port], got {origin!r}")
        elif url.scheme == "http" and url.hostname not in LOCAL_HOSTS:
            problems.append("MOCKTRADE_ORIGIN must be https:// (plain http only for localhost)")
        port_text = env.get("MOCKTRADE_PORT", "8000")
        port = int(port_text) if port_text.isdigit() and 0 < int(port_text) < 65536 else 0
        if not port:
            problems.append(f"MOCKTRADE_PORT must be a port number, got {port_text!r}")
        prices = env.get("MOCKTRADE_PRICES", "sim")
        if prices not in PRICE_SOURCES:
            problems.append(
                f"MOCKTRADE_PRICES must be one of {', '.join(PRICE_SOURCES)}, got {prices!r}"
            )
        kite_env = tuple(sorted((k, v) for k, v in env.items() if k.startswith("KITE_") and v))
        if prices == "kite" and not any(k.startswith("KITE_API_KEY") for k, _ in kite_env):
            problems.append("MOCKTRADE_PRICES=kite needs KITE_API_KEY and KITE_API_SECRET")
        holidays: frozenset = frozenset()
        try:
            holidays = parse_holidays(env.get("MOCKTRADE_HOLIDAYS", ""))
        except ValueError:
            problems.append("MOCKTRADE_HOLIDAYS must be dates like 2026-10-21,2026-11-10")
        hook = env.get("MOCKTRADE_ALERT_WEBHOOK", "")
        if hook and not hook.startswith("https://"):
            problems.append("MOCKTRADE_ALERT_WEBHOOK must be an https:// address")
        funds_text = env.get("MOCKTRADE_PAPER_FUNDS", "1000000")
        if not funds_text.isdigit() or int(funds_text) <= 0:
            problems.append(
                f"MOCKTRADE_PAPER_FUNDS must be whole rupees above 0, got {funds_text!r}"
            )
        record_text = env.get("MOCKTRADE_RECORD_DAYS", "30")
        if not record_text.isdigit():
            problems.append(f"MOCKTRADE_RECORD_DAYS must be a whole number, got {record_text!r}")
        charges_text = env.get("MOCKTRADE_CHARGES", "")
        if charges_text:
            try:
                load_schedule(charges_text)
            except (OSError, ValueError) as exc:
                problems.append(f"MOCKTRADE_CHARGES: {exc}")
        algo_ids: dict[str, str] = {}
        try:
            algo_ids = parse_algo_ids(env.get("MOCKTRADE_ALGO_IDS", ""))
        except ValueError as exc:
            problems.append(str(exc))
        unknown = sorted(set(algo_ids) - {t.spec.code for t in BUILTIN})
        if unknown:
            problems.append(f"MOCKTRADE_ALGO_IDS: no algo called {', '.join(unknown)}")
        if problems:
            raise ConfigError("; ".join(problems))
        return cls(
            secret_key=key,
            origin=origin,
            data_dir=Path(env.get("MOCKTRADE_DATA", "data")),
            host=env.get("MOCKTRADE_HOST", "127.0.0.1"),
            port=port,
            ui_dir=Path(env.get("MOCKTRADE_UI", "ui/dist")),
            prices=prices,
            trusted_proxies=env.get("MOCKTRADE_TRUSTED_PROXIES", ""),
            holidays=holidays,
            alert_webhook=env.get("MOCKTRADE_ALERT_WEBHOOK", ""),
            kite_env=kite_env,
            paper_funds_paise=int(funds_text) * 100,
            charges_file=Path(charges_text) if charges_text else None,
            record_days=int(record_text) if record_text.isdigit() else 30,
            algo_ids=tuple(sorted(algo_ids.items())),
        )


@dataclass
class Assembly:
    """Everything one server runs, with what must start and stop alongside the web app."""

    app: FastAPI
    platform: Platform
    users: UserStore
    log: EventLog
    jobs: list = field(default_factory=list)  # coroutines to run while serving
    brokers: list = field(default_factory=list)
    running: list[str] = field(default_factory=list)  # account ids
    not_running: dict[str, str] = field(default_factory=dict)  # account id -> why
    kite: KiteLive | None = None
    alerts: Alerts | None = None
    notifier: WebhookNotifier | None = None

    async def close(self) -> None:
        if self.notifier is not None:
            await self.notifier.aclose()
        if self.kite is not None:
            await self.kite.aclose()
        for broker in self.brokers:
            with contextlib.suppress(Exception):
                await broker.aclose()
        await self.log.aclose()  # flushes the audit trail


def build(settings: ServeSettings) -> Assembly:
    """Assemble the server. Raises ConfigError (naming the setting) before starting anything
    if an account can't be run as configured."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    users = UserStore(settings.data_dir / "users.db", SecretBox(settings.secret_key))
    instruments = InstrumentStore()
    md = MarketData()
    jobs = []
    kite: KiteLive | None = None
    accounts = users.accounts()
    if settings.prices == "kite":
        for account in accounts:  # every kite account has an app before anything starts
            if account.broker == "kite":
                settings.kite_app(account)
        platform_ref: list[Platform] = []

        def resync(account_id: str) -> None:
            running = platform_ref[0].account(account_id) if platform_ref else None
            if running is not None:
                asyncio.ensure_future(_resync(account_id, running.orders))

        kite = KiteLive(
            settings.kite_app,
            settings.data_dir,
            instruments,
            md,
            instruments_cache=settings.data_dir / "kite_instruments.csv",
            on_reconnect=resync,
            api_url=settings.kite_url("KITE_API_URL", BASE_URL),
            ws_url=settings.kite_url("KITE_WS_URL", WS_URL),
        )
        jobs.append(kite.start_and_run)
    else:
        for instrument, _ in INSTRUMENTS:
            instruments.add(instrument)
        simulator = MarketSimulator(md, dict(INSTRUMENTS))
        simulator.step()  # every contract has a price before the first order
        jobs.append(simulator.run)

    events = EventLog(settings.data_dir / "events.db")
    events.start()
    platform = Platform(instruments, md, KillSwitch("firm"), events)
    if settings.charges_file is not None:
        platform.charges_model = ScheduleCharges(load_schedule(settings.charges_file))
    brokers = []
    running: list[str] = []
    not_running: dict[str, str] = {}
    day = f"S{time.strftime('%y%m%d')}"
    ids = ClientIds(day, events.last_counter(day))  # one sequence for the day, all accounts

    def start(account: BrokerAccount, limits: dict | None) -> str | None:
        """Run one account (at start, or an admin's new one); None, or why it can't run."""
        if account.broker == "paper":
            broker = PaperBroker(
                md, instruments, name="paper", starting_paise=settings.paper_funds_paise
            )
            broker.start()
            brokers.append(broker)
        elif account.broker == "kite" and kite is not None and account.id in kite_ready:
            broker = kite.add_account(account)  # refuses orders until its daily login
        elif account.broker == "kite" and kite is not None:
            return "a Kite account starts after a restart (its app key in the settings first)"
        else:
            return f"{account.broker} accounts need MOCKTRADE_PRICES=kite"
        platform.add_account(
            account, broker, ids=ids, auto_kill_on_loss=True,
            algo_types=tuple(with_exchange_ids(BUILTIN, dict(settings.algo_ids))),
            recover_on_start=True,  # no new orders until rebuilt and checked (recovery.py)
            margin_check=True,  # refuse what the funds can't carry (risk/margin.py; fails open)
            rules_dir=settings.data_dir / "rules",  # the trader's MTM limits survive a restart
            limits=stored_limits(limits),  # an admin's limits (K13.1), else the defaults
        )  # fmt: skip
        running.append(account.id)
        return None

    kite_ready = {a.id for a in accounts if a.broker == "kite"}  # their apps were checked above
    for account in accounts:
        why = start(account, users.account_limits(account.id))
        if why:
            not_running[account.id] = why
            log.warning("account %s not started: %s", account.id, why)
    starter = CallbackStarter(("paper", "kite") if kite is not None else ("paper",), start,
                              not_running)  # fmt: skip
    if kite is not None:
        platform_ref.append(platform)
    jobs.append(platform.run)
    jobs.append(watch_loop_lag)

    # daily operation: alerts to the screens, the audit trail and (optionally) a phone
    alerts = Alerts()
    alerts.subscribe(
        lambda a: events.record_note(
            "OPS_ALERT" if a.active else "OPS_CLEAR", "-", a.text, {"key": a.key, "level": a.level}
        )
    )
    notifier = WebhookNotifier(settings.alert_webhook) if settings.alert_webhook else None
    if notifier is not None:
        alerts.subscribe(notifier)
    for account_id in running:
        gate = platform.accounts[account_id].recovery
        if gate is not None:
            gate.subscribe(
                lambda state, reason, a=account_id: _recovery_alert(alerts, a, state, reason)
            )
    checks = Checks(
        alerts,
        md,
        accounts,
        settings.data_dir,
        broker_status=kite.status if kite else None,
        prices_expected=(lambda: kite.any_live) if kite else (lambda: True),
    )
    scheduler = Scheduler(holidays=settings.holidays)
    if kite is not None:
        scheduler.daily("08:30", kite.refresh_instruments, "instrument refresh")
        scheduler.every(60, checks.kite_login, between=("08:30", "15:30"), name="kite login")
    scheduler.every(15, checks.prices, between=("09:16", "15:30"), name="prices")
    scheduler.every(300, checks.disk, between=("00:00", "23:59"), name="disk")
    reports = Reports(
        platform,
        ReportStore(settings.data_dir / "reports"),
        is_trading_day=scheduler.is_trading_day,
    )
    scheduler.daily("08:45", platform.roll_day, "new trading day")  # #266: no restart needed
    scheduler.daily("16:00", reports.close_of_day, "end-of-day reports")
    ticks = None
    if settings.record_days > 0:
        ticks = FileTickStore(
            settings.data_dir / "ticks", keep_days=settings.record_days, describe=instruments.get
        )  # each day keeps its instrument list
        recorder = TickRecorder(ticks)
        md.tap(recorder.on_tick)
        jobs.append(recorder.run)
        scheduler.daily("16:30", _rotate(ticks), "tick recordings rotation")
    candle_store = CandleStore(settings.data_dir / "candles")
    backtests = Backtests(
        settings.data_dir / "backtests", instruments, platform.charges_model,
        ticks=(lambda d: ticks.read_timed(d) if ticks and d in ticks.days() else None),
        candles=candle_store.rows,
    )  # fmt: skip
    # charts: live candles from every tick; history from Kite (or the recordings on paper)
    builder = CandleBuilder()
    md.tap(builder.on_tick)
    if kite is not None:
        history = KiteHistory(kite, lambda i: instruments.to_broker(KITE, i))
        history_name = "Kite historical data"
    elif ticks is not None:
        history, history_name = RecordedHistory(ticks), "recorded ticks"
    else:
        history, history_name = None, ""
    charts = Charts(builder, history, events=events, history_name=history_name)
    market_alerts = build_market_alerts(platform, settings.data_dir / "alerts")
    jobs.append(market_alerts.store.run)
    guards = DayGuards(platform, alerts)
    scheduler.every(60, guards.check, between=("09:15", "15:35"), name="day guards")
    scheduler.every(
        60, guards.check_mtm_job, between=("00:00", "23:59"), name="mtm closes", any_day=True
    )
    # stops held by MockTrade that a restart (or a pause, stop or failure) left off an open
    # position: one band per position until it is flat or protected again (V1.2)
    protection = ProtectionGuard(platform, alerts)
    protection.after_restart(running)
    scheduler.every(
        5, protection.run_check, between=("00:00", "23:59"), name="protection guard", any_day=True
    )
    scheduler.every(5, lambda: audit_log(alerts, events), between=("00:00", "23:59"),
                    name="audit log", any_day=True)  # fmt: skip
    overload = PriceOverload(alerts, md)
    scheduler.every(15, overload.check, between=("00:00", "23:59"), name="price overload",
                    any_day=True)  # fmt: skip
    jobs.append(scheduler.run)
    ui = settings.ui_dir if (settings.ui_dir / "index.html").exists() else None
    if ui is None:
        log.warning("no built screens in %s: serving the API only", settings.ui_dir)
    app = create_app(
        platform,
        users,
        ServerSettings(origin=settings.origin, cookie_secure=settings.cookie_secure),
        broker_login=kite.login if kite else None,
        broker_status=kite.status if kite else None,
        alerts=alerts,
        protection=protection,
        scheduler=scheduler,
        reports=reports,
        ticks=ticks,
        backtests=backtests,
        candles=candle_store,
        charts=charts,
        market_alerts=market_alerts,
        starter=starter,
        static_dir=ui,
    )
    built = Assembly(app, platform, users, events, jobs, brokers, running, not_running, kite)
    built.alerts, built.notifier = alerts, notifier
    return built


def _rotate(store: FileTickStore):
    async def rotate() -> None:
        await asyncio.to_thread(store.rotate)

    return rotate


def _recovery_alert(alerts: Alerts, account_id: str, state: str, reason: str) -> None:
    """A failed recovery keeps the account shut: someone must look, so say it loudly."""
    key = f"recovery:{account_id}"
    if state == "failed":
        alerts.raise_(key, "ERROR", f"{account_id}: trading stays paused after a restart: {reason}",
                      account_id=account_id)  # fmt: skip
    elif state == "ok":
        alerts.clear(key, f"{account_id}: orders and positions rebuilt after the restart")


async def _resync(account_id: str, orders) -> None:
    """After a broker reconnect: take the broker's order book as the truth again."""
    try:
        changed = await orders.resync()
        if changed:
            log.info("account %s: %d orders updated after reconnect", account_id, len(changed))
    except Exception as exc:
        log.warning("account %s: resync after reconnect failed: %r", account_id, exc)


class _Server(uvicorn.Server):
    """uvicorn re-raises SIGTERM after its own shutdown, which would end the process before
    the audit trail is flushed. Here a signal only asks the server to stop; `run` then closes
    everything in order and the process exits 0 (what `docker stop` expects)."""

    @contextlib.contextmanager
    def capture_signals(self):
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        for sig in signals:
            loop.add_signal_handler(sig, self.handle_exit, sig, None)
        try:
            yield
        finally:
            for sig in signals:
                loop.remove_signal_handler(sig)


async def run(settings: ServeSettings) -> None:
    built = build(settings)
    freeze_heap()  # start-up objects never slow a garbage collection again (metrics.py)
    config = uvicorn.Config(
        built.app,
        host=settings.host,
        port=settings.port,
        proxy_headers=bool(settings.trusted_proxies),
        forwarded_allow_ips=settings.trusted_proxies or None,
        server_header=False,
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    server = _Server(config)
    tasks = [asyncio.create_task(job()) for job in built.jobs]
    log.info(
        "MockTrade on %s:%s for %s (prices: %s; %d accounts running, %d not)",
        settings.host,
        settings.port,
        settings.origin,
        settings.prices,
        len(built.running),
        len(built.not_running),
    )
    try:
        await server.serve()  # returns on SIGTERM / SIGINT after open requests finish
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await built.close()
        log.info("stopped cleanly")


def main(argv: list[str], env: Mapping[str, str]) -> int:
    parser = argparse.ArgumentParser(prog="mocktrade.tools.serve")
    parser.add_argument("--check", action="store_true", help="check the settings and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = ServeSettings.from_env(env)
    except ConfigError as exc:
        print(f"settings: {exc}", file=sys.stderr)
        return 2
    os.umask(0o077)  # databases and broker tokens: readable by this user only
    if args.check:
        print(
            f"settings OK: {settings.origin} on {settings.host}:{settings.port}, "
            f"data in {settings.data_dir}, prices {settings.prices}"
        )
        return 0
    try:
        asyncio.run(run(settings))
    except ConfigError as exc:
        print(f"settings: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], environment()))
