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

from mocktrade.algos.builtin import BUILTIN
from mocktrade.broker.kite.http import BASE_URL
from mocktrade.broker.kite.live import KiteLive
from mocktrade.broker.kite.ticker import WS_URL
from mocktrade.broker.paper import PaperBroker
from mocktrade.config import ConfigError, KiteSettings, environment
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sim_universe import INSTRUMENTS
from mocktrade.marketdata.simulator import MarketSimulator
from mocktrade.metrics import freeze_heap, watch_loop_lag
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
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
    kite_env: tuple[tuple[str, str], ...] = ()  # the KITE_* settings (kite mode)

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
            kite_env=kite_env,
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

    async def close(self) -> None:
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
    brokers = []
    running: list[str] = []
    not_running: dict[str, str] = {}
    day = f"S{time.strftime('%y%m%d')}"
    ids = ClientIds(day, events.last_counter(day))  # one sequence for the day, all accounts
    for account in accounts:
        if account.broker == "paper":
            broker = PaperBroker(md, instruments, name="paper")
            broker.start()
            brokers.append(broker)
        elif account.broker == "kite" and kite is not None:
            broker = kite.add_account(account)  # refuses orders until its daily login
        else:
            not_running[account.id] = f"{account.broker} accounts need MOCKTRADE_PRICES=kite"
            log.warning("account %s not started: %s", account.id, not_running[account.id])
            continue
        platform.add_account(account, broker, ids=ids, auto_kill_on_loss=True, algo_types=BUILTIN)
        running.append(account.id)
    if kite is not None:
        platform_ref.append(platform)
    jobs.append(platform.run)
    jobs.append(watch_loop_lag)
    ui = settings.ui_dir if (settings.ui_dir / "index.html").exists() else None
    if ui is None:
        log.warning("no built screens in %s: serving the API only", settings.ui_dir)
    app = create_app(
        platform,
        users,
        ServerSettings(origin=settings.origin, cookie_secure=settings.cookie_secure),
        broker_login=kite.login if kite else None,
        broker_status=kite.status if kite else None,
        static_dir=ui,
    )
    return Assembly(app, platform, users, events, jobs, brokers, running, not_running, kite)


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
