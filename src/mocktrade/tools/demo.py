"""Run the whole platform locally with simulated prices and paper trading: no Kite needed.

    python -m mocktrade.tools.demo                  # http://localhost:8000
    python -m mocktrade.tools.demo --code           # today's 6-digit login code
    python -m mocktrade.tools.demo --origin http://localhost:5173   # with the UI dev server

Everything lives in data/demo/ (git-ignored): a demo user, a paper account, a throwaway
secret key. Prices come from the market simulator; the paper broker fills against them
exactly as it would against Kite. For development and showing the screens only.
"""

import argparse
import asyncio
import contextlib
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import uvicorn

from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.simulator import MarketSimulator
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.server import security
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog

DATA = Path("data/demo")
USER, PASSWORD = "demo", "demo-password-123"
EXPIRY = date(2026, 10, 27)
K = InstrumentKind

INSTRUMENTS = [
    (Instrument(Exchange.NFO, "NIFTY26OCTFUT", K.FUTURE, 10, 75, EXPIRY, None, "NIFTY"), 2501000),
    (
        Instrument(
            Exchange.NFO, "NIFTY26NOVFUT", K.FUTURE, 10, 75, date(2026, 11, 24), None, "NIFTY"
        ),
        2514860,
    ),
    (
        Instrument(Exchange.NFO, "BANKNIFTY26OCTFUT", K.FUTURE, 20, 35, EXPIRY, None, "BANKNIFTY"),
        5621460,
    ),
    (Instrument(Exchange.NFO, "NIFTY26OCT25000CE", K.CALL, 5, 75, EXPIRY, 2500000, "NIFTY"), 12005),
    (Instrument(Exchange.NFO, "NIFTY26OCT25000PE", K.PUT, 5, 75, EXPIRY, 2500000, "NIFTY"), 9540),
    (Instrument(Exchange.NSE, "RELIANCE", K.EQUITY, 10, 1), 140020),
    (Instrument(Exchange.NSE, "HDFCBANK", K.EQUITY, 10, 1), 174280),
    (Instrument(Exchange.NSE, "INFY", K.EQUITY, 10, 1), 150340),
    (Instrument(Exchange.NSE, "SBIN", K.EQUITY, 5, 1), 87325),
    (Instrument(Exchange.NSE, "ICICIBANK", K.EQUITY, 10, 1), 128600),
]


def secret_key() -> str:
    path = DATA / "secret.key"
    if not path.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        path.write_text(SecretBox.new_key())
        path.chmod(0o600)
    return path.read_text().strip()


def demo_totp_secret() -> str:
    return (DATA / "demo_totp.txt").read_text().strip()


def setup_users() -> UserStore:
    store = UserStore(DATA / "users.db", SecretBox(secret_key()))
    if store.get_user(USER) is None:
        created = store.create_user(USER, "Demo Trader", PASSWORD, {Role.ADMIN, Role.TRADER})
        secret = parse_qs(urlparse(created.totp_uri).query)["secret"][0]
        (DATA / "demo_totp.txt").write_text(secret)  # demo only: real secrets are never kept
        store.add_account(BrokerAccount("acc-paper", "paper", "PAPER1", USER))
    return store


async def main(port: int, origin: str, ui: Path | None) -> None:
    users = setup_users()
    instruments = InstrumentStore()
    for instrument, _ in INSTRUMENTS:
        instruments.add(instrument)
    md = MarketData()
    simulator = MarketSimulator(md, {i: p for i, p in INSTRUMENTS})
    simulator.step()  # every instrument has a price before the first order
    log = EventLog(DATA / "events.db")
    log.start()
    platform = Platform(instruments, md, KillSwitch("firm"), log)
    brokers = []
    for account in users.accounts():
        broker = PaperBroker(md, instruments, name="paper")
        broker.start()
        brokers.append(broker)
        ids = ClientIds(
            f"D{time.strftime('%y%m%d')}", log.last_counter(f"D{time.strftime('%y%m%d')}")
        )
        platform.add_account(account, broker, ids=ids, auto_kill_on_loss=True)
    settings = ServerSettings(origin=origin, cookie_secure=False)
    app = create_app(platform, users, settings, static_dir=ui)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    print(f"\nMockTrade demo on http://localhost:{port}  (pages from {origin})")
    print(f"user: {USER}   password: {PASSWORD}   code: python -m mocktrade.tools.demo --code\n")
    background = [asyncio.create_task(simulator.run()), asyncio.create_task(platform.run())]
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="mocktrade.tools.demo")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--origin", default=None, help="where the pages are served from")
    parser.add_argument("--ui", type=Path, default=Path("ui/dist"))
    parser.add_argument("--code", action="store_true", help="print the current login code")
    args = parser.parse_args()
    if args.code:
        setup_users()
        print(security.totp_code(demo_totp_secret(), time.time()))
    else:
        ui = args.ui if (args.ui / "index.html").exists() else None
        asyncio.run(main(args.port, args.origin or f"http://localhost:{args.port}", ui))
