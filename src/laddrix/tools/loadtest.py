"""Load test: the real server in its own process, fed by a fake Kite, read by screen-like clients.

    python -m laddrix.tools.loadtest                       # 1000 contracts, 2 ticks/s each
    python -m laddrix.tools.loadtest --instruments 3000 --rate 1 --clients 10 --seconds 60

    this process (the driver)                        server process: python -m laddrix.tools.serve
    fake Kite REST: /instruments /orders /session ◄── KiteHttp (instrument dump, orders)
    fake Kite ticker: 184-byte "full" frames ───────► KiteTicker ► KiteFeed ► MarketData ► StreamHub
    N screen clients (WebSocket, 200 contracts each) ◄──────────────────────────────── StreamHub
    orders over HTTP ───────────────────────────────► OrderManager ► KiteBroker ► fake Kite REST

The server runs exactly as in production (kite mode, today's login saved), only pointed at the
fake Kite (KITE_API_URL / KITE_WS_URL). Every round each contract gets a fresh price, so a
client knows when the driver sent it: "end to end" is fake Kite → server → screen, through
decode, conflation and two sockets. Both processes read the same system-wide monotonic clock.
The server's own stages come from its /api/metrics; its CPU and memory from the same.

Prints a markdown table (docs/design/performance.md), or JSON with --json.
"""

import argparse
import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from websockets.asyncio.client import connect

from laddrix.broker.kite import token_store
from laddrix.broker.kite.encode import frame, full_packet, nfo_token
from laddrix.broker.kite.session import KiteSession
from laddrix.domain.accounts import BrokerAccount, Role
from laddrix.metrics import Histogram
from laddrix.server import security
from laddrix.server.security import SecretBox
from laddrix.server.users import UserStore

PASSWORD = "load-test-password-1"
PER_CONNECTION = 200  # the server's limit per screen connection
BASE = 2500000  # ₹25,000.00
CLIENT_ID = "LOAD01"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def symbol(n: int) -> str:
    return f"LOAD{n:05d}FUT"


class FakeKite:
    """Kite's REST and ticker, just enough for the server to run in kite mode."""

    def __init__(self, instruments: int) -> None:
        self.n = instruments
        self.sockets: set[WebSocket] = set()
        self.round = 0
        self.made: dict[tuple[str, int], int] = {}  # (our id, ltp) -> perf_counter_ns sent
        self.orders = 0
        self.app = self._app()

    def price(self, n: int) -> int:
        return BASE + 10 * ((self.round * 7 + n) % 5000)  # unique per contract for 5000 rounds

    def csv(self) -> str:
        expiry = (date.today() + timedelta(days=30)).isoformat()
        rows = [
            "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
            "tick_size,lot_size,instrument_type,segment,exchange"
        ]
        for n in range(1, self.n + 1):
            rows.append(f"{nfo_token(n)},{n},{symbol(n)},LOAD,0,{expiry},0,0.1,75,FUT,NFO-FUT,NFO")
        return "\n".join(rows) + "\n"

    def _app(self) -> FastAPI:
        app = FastAPI()

        def ok(data):
            return {"status": "success", "data": data}

        @app.get("/instruments", response_class=PlainTextResponse)
        async def instruments():
            return self.csv()

        @app.post("/orders/{variety}")
        async def place(variety: str):
            self.orders += 1
            return ok({"order_id": str(100000 + self.orders)})

        @app.delete("/orders/{variety}/{order_id}")
        async def cancel(variety: str, order_id: str):
            return ok({"order_id": order_id})

        @app.get("/orders")
        async def orders():
            return ok([])

        @app.get("/portfolio/positions")
        async def positions():
            return ok({"net": [], "day": []})

        @app.websocket("/ws")
        async def ticker(ws: WebSocket):
            await ws.accept()
            self.sockets.add(ws)
            try:
                while True:
                    await ws.receive_text()  # subscribe/mode requests: every contract is sent
            except WebSocketDisconnect:
                pass
            finally:
                self.sockets.discard(ws)

        return app

    async def tick(self) -> int:
        """One round: every contract moves; one frame to every connected ticker."""
        self.round += 1
        packets, prices = [], []
        for n in range(1, self.n + 1):
            ltp = self.price(n)
            prices.append(ltp)
            packets.append(
                full_packet(
                    nfo_token(n),
                    ltp,
                    volume=self.round * 75,
                    bids=tuple((ltp - 10 * k, 150, 2) for k in range(1, 6)),
                    asks=tuple((ltp + 10 * k, 225, 3) for k in range(1, 6)),
                    exchange_time=int(time.time()),
                )
            )
        data = frame(packets)
        now = time.perf_counter_ns()  # stamped after building: the server's work starts here
        for n, ltp in enumerate(prices, 1):
            self.made[(f"NFO:{symbol(n)}", ltp)] = now
        for ws in list(self.sockets):
            with contextlib.suppress(Exception):
                await ws.send_bytes(data)
        if self.round % 20 == 0:
            cutoff = now - 20_000_000_000
            self.made = {k: v for k, v in self.made.items() if v >= cutoff}
        return len(packets)


def prepare(data: Path, key: str) -> str:
    """The server's data: an admin trader, a kite account logged in for today."""
    users = UserStore(data / "users.db", SecretBox(key))
    created = users.create_user("load", "Load", PASSWORD, {Role.ADMIN, Role.TRADER})
    users.add_account(BrokerAccount("acc-kite", "kite", CLIENT_ID, "load"))
    token_store.save(
        data / "kite_token_acc-kite.json", KiteSession(CLIENT_ID, "load-token"), datetime.now(UTC)
    )
    return parse_qs(urlparse(created.totp_uri).query)["secret"][0]


async def screen(url, origin, cookie, ids, kite: FakeKite, e2e: Histogram, counts: dict):
    headers = {"Cookie": cookie}
    async with connect(url, origin=origin, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({"op": "subscribe", "instruments": ids}))
        async for text in ws:
            now = time.perf_counter_ns()
            message = json.loads(text)
            if message.get("type") != "ticks":
                continue
            for t in message["ticks"]:
                made = kite.made.get((t["instrument_id"], t["ltp_paise"]))
                if made is not None:
                    e2e.observe((now - made) / 1000)
                counts["ticks"] += 1
            counts["messages"] += 1


async def run(
    instruments: int,
    rate: float,
    clients: int,
    seconds: float,
    orders_per_s: float,
    profile: str | None = None,
):
    kite = FakeKite(instruments)
    kite_port, port = free_port(), free_port()
    fake = uvicorn.Server(
        uvicorn.Config(kite.app, host="127.0.0.1", port=kite_port, log_level="warning")
    )
    fake_task = asyncio.create_task(fake.serve())
    data = Path(tempfile.mkdtemp(prefix="laddrix-load-"))
    key = SecretBox.new_key()
    secret = prepare(data, key)
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "LADDRIX_SECRET_KEY": key,
        "LADDRIX_ORIGIN": origin,
        "LADDRIX_PORT": str(port),
        "LADDRIX_DATA": str(data),
        "LADDRIX_UI": str(data / "no-ui"),
        "LADDRIX_PRICES": "kite",
        "KITE_API_KEY": "load",
        "KITE_API_SECRET": "load",
        "KITE_API_URL": f"http://127.0.0.1:{kite_port}",
        "KITE_WS_URL": f"ws://127.0.0.1:{kite_port}/ws",
    }
    log = open(data / "server.log", "w")  # noqa: SIM115 (closed below)
    server = subprocess.Popen(
        [
            sys.executable,
            *(["-m", "cProfile", "-o", profile] if profile else []),
            "-m",
            "laddrix.tools.serve",
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    tasks: list[asyncio.Task] = []
    http = httpx.AsyncClient(base_url=origin, headers={"origin": origin}, timeout=10)
    try:
        for _ in range(200):
            with contextlib.suppress(httpx.HTTPError):
                if (await http.get("/api/health")).status_code == 200:
                    break
            await asyncio.sleep(0.05)
        code = security.totp_code(secret, time.time())
        login = {"username": "load", "password": PASSWORD, "code": code}
        (await http.post("/api/session", json=login)).raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
        for _ in range(200):  # the server downloads the fake instrument list when it goes live
            found = (await http.get("/api/instruments", params={"q": "LOAD", "limit": 1})).json()
            if found:
                break
            await asyncio.sleep(0.05)
        ids = [f"NFO:{symbol(n)}" for n in range(1, instruments + 1)]
        per = min(PER_CONNECTION, instruments)
        e2e, orders_ms = Histogram("e2e", "us"), Histogram("orders", "ms")
        counts = {"ticks": 0, "messages": 0}
        ws_url = f"ws://127.0.0.1:{port}/ws"
        for c in range(clients):
            mine = [ids[(c * per + k) % instruments] for k in range(per)]
            tasks.append(
                asyncio.create_task(screen(ws_url, origin, cookie, mine, kite, e2e, counts))
            )
        for _ in range(3):  # warm-up rounds: connections, subscriptions, first prices
            await kite.tick()
            await asyncio.sleep(1 / rate)
        await http.delete("/api/metrics")
        before = (await http.get("/api/metrics")).json()
        e2e.reset()
        counts.update(ticks=0, messages=0)

        async def place_orders():
            n = 0
            while orders_per_s > 0:
                n += 1
                i = n % instruments
                body = {
                    "instrument_id": ids[i],
                    "side": "BUY",
                    "qty": 75,
                    "order_type": "LIMIT",
                    "product": "NRML",
                    "price_paise": kite.price(i + 1) - 500,  # ₹5 under: rests
                }
                started = time.perf_counter()
                r = await http.post("/api/accounts/acc-kite/orders", json=body)
                if r.status_code == 201:
                    orders_ms.observe((time.perf_counter() - started) * 1000)
                    cid = r.json()["request"]["client_order_id"]
                    await http.delete(f"/api/accounts/acc-kite/orders/{cid}")
                await asyncio.sleep(1 / orders_per_s)

        tasks.append(asyncio.create_task(place_orders()))
        sent, wall0 = 0, time.perf_counter()
        next_at = wall0
        while time.perf_counter() - wall0 < seconds:
            sent += await kite.tick()
            next_at += 1 / rate
            await asyncio.sleep(max(0.0, next_at - time.perf_counter()))
        await asyncio.sleep(0.5)  # the last round reaches the screens
        wall = time.perf_counter() - wall0
        after = (await http.get("/api/metrics")).json()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http.aclose()
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(10)
        log.close()
        fake.should_exit = True
        await asyncio.gather(fake_task, return_exceptions=True)
    cpu = after["gauges"]["process_cpu_seconds"] - before["gauges"]["process_cpu_seconds"]
    return {
        "setup": {
            "instruments": instruments,
            "rate": rate,
            "clients": clients,
            "per_client": per,
            "seconds": seconds,
            "orders_per_s": orders_per_s,
            "cpus": os.cpu_count(),
        },
        "ticks_in_per_s": round(sent / wall),
        "ticks_out_per_s": round(counts["ticks"] / wall),
        "server_cpu_percent": round(100 * cpu / wall, 1),
        "server_rss_mb": after["gauges"].get("process_rss_mb"),
        "e2e_us": e2e.snapshot(),
        "order_ms": orders_ms.snapshot(),
        "server": after["histograms"],
        "server_log": str(data / "server.log"),
    }


def table(r: dict) -> str:
    s = r["setup"]

    def q(h: dict | None, unit: str) -> str:
        if not h or not h.get("count"):
            return "–"

        def f(v: float) -> str:
            ms = v / 1000 if unit == "us" else v
            return f"{ms:.1f} ms" if ms >= 1 else f"{ms * 1000:.0f} µs"

        return f"p50 {f(h['p50'])} · p99 {f(h['p99'])} · max {f(h['max'])} (n={h['count']:,})"

    srv = r["server"]
    order_route = "http_us{route=POST /api/accounts/{account_id}/orders}"
    rows = [
        (
            "Setup",
            f"{s['instruments']:,} contracts × {s['rate']:g} ticks/s · {s['clients']} screens × "
            f"{s['per_client']} contracts · {s['orders_per_s']:g} orders/s · {s['seconds']:g} s"
            f" · {s['cpus']} CPUs",
        ),
        ("Ticks in → out per second", f"{r['ticks_in_per_s']:,} → {r['ticks_out_per_s']:,}"),
        ("Server CPU (one core) · memory", f"{r['server_cpu_percent']}% · {r['server_rss_mb']} MB"),
        ("Kite frame: decode + fan-out", q(srv.get("kite_frame_us"), "us")),
        ("Decode, per 250-tick chunk (longest hold-up)", q(srv.get("kite_chunk_us"), "us")),
        ("Fan-out of a frame's ticks", q(srv.get("kite_fanout_us"), "us")),
        ("Garbage collector pauses (full)", q(srv.get("gc_pause_ms{gen=2}"), "ms")),
        ("Tick at server → screen's socket", q(srv.get("stream_tick_age_us"), "us")),
        ("**Kite → screen, end to end**", q(r["e2e_us"], "us")),
        ("**Order: click → Kite ack → screen**", q(r["order_ms"], "ms")),
        ("KiteBroker → Kite REST ack", q(srv.get("broker_ms{op=place,broker=kite}"), "ms")),
        ("Order request inside the server", q(srv.get(order_route), "us")),
        ("Event loop lag", q(srv.get("loop_lag_ms"), "ms")),
    ]
    out = ["| Measure | Result |", "|---|---|"]
    out += [f"| {a} | {b} |" for a, b in rows]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(prog="laddrix.tools.loadtest")
    parser.add_argument("--instruments", type=int, default=1000)
    parser.add_argument("--rate", type=float, default=2.0, help="ticks per second per contract")
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--orders", type=float, default=2, help="orders per second (+ cancels)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile", help="write a cProfile of the server process to this file")
    args = parser.parse_args()
    result = asyncio.run(
        run(args.instruments, args.rate, args.clients, args.seconds, args.orders, args.profile)
    )
    print(json.dumps(result, indent=2) if args.json else table(result))


if __name__ == "__main__":
    main()
