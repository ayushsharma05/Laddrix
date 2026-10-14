"""The browser stream against a real server: conflation, ordering, permissions, safety."""

import asyncio
import json
import socket
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from mocktrade.algos.builtin import BUILTIN
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.ops.alerts import Alerts
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.server import security
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.stream import CLOSE_NOT_LOGGED_IN, CLOSE_TOO_SLOW
from mocktrade.server.users import UserStore

FUT = "NFO:NIFTY26OCTFUT"
PASSWORD = "a long enough password"
ORDER = {
    "instrument_id": FUT,
    "side": "BUY",
    "qty": 75,
    "order_type": "LIMIT",
    "product": "NRML",
    "price_paise": 2501000,
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Live:
    """A running server (plain http on localhost, as in development)."""

    def __init__(self, tmp_path, nifty_fut):
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.users = UserStore(tmp_path / "users.db", SecretBox(SecretBox.new_key()))
        self.secrets = {}
        for user_id, roles in (("ayush", {Role.TRADER}), ("riya", {Role.TRADER})):
            created = self.users.create_user(user_id, user_id.title(), PASSWORD, roles)
            self.secrets[user_id] = parse_qs(urlparse(created.totp_uri).query)["secret"][0]
            self.users.add_account(BrokerAccount(f"acc-{user_id}", "kite", user_id, user_id))
        self.store = InstrumentStore()
        self.store.add(nifty_fut)
        self.md = MarketData()
        self.md.on_tick(Tick(FUT, 2501000))
        self.platform = Platform(self.store, self.md, KillSwitch("firm"))
        for account in self.users.accounts():
            self.platform.add_account(
                account, FakeBroker(), ids=ClientIds("MT"), algo_types=BUILTIN
            )
        self.alerts = Alerts()
        self.app = create_app(
            self.platform,
            self.users,
            ServerSettings(origin=self.origin, cookie_secure=False),
            alerts=self.alerts,
        )
        self.hub = self.app.state.hub
        self.hub.flush_s = 0.05  # faster than the real 250 ms, so tests are quick
        self.used_steps = {}

    async def start(self):
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)

    async def stop(self):
        self.server.should_exit = True
        await self.task

    async def session(self, user_id="ayush") -> httpx.AsyncClient:
        client = httpx.AsyncClient(base_url=self.origin, headers={"origin": self.origin})
        # each code works once: a second login for the same user uses the next 30 s step
        logins = self.used_steps.get(user_id, 0)
        self.used_steps[user_id] = logins + 1
        assert logins < 2, "the 2FA window allows two logins per user per test"
        code = security.totp_code(
            self.secrets[user_id], datetime.now(UTC).timestamp() + 30 * logins
        )
        r = await client.post(
            "/api/session", json={"username": user_id, "password": PASSWORD, "code": code}
        )
        assert r.status_code == 200, r.text
        return client

    def stream(self, client: httpx.AsyncClient):
        cookie = f"mt_session={client.cookies['mt_session']}"
        return connect(
            f"ws://127.0.0.1:{self.port}/ws",
            origin=self.origin,
            additional_headers={"Cookie": cookie},
        )


@pytest.fixture
async def live(tmp_path, nifty_fut):
    server = Live(tmp_path, nifty_fut)
    await server.start()
    yield server
    await server.stop()


async def next_of(ws, kind, timeout=2.0):
    """The next message of one type (others are skipped)."""
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind:
                return message


async def all_of(ws, kind, quiet=0.3):
    """Every message of one type until the stream is quiet for `quiet` seconds."""
    found = []
    while True:
        try:
            message = json.loads(await asyncio.wait_for(ws.recv(), quiet))
        except TimeoutError:
            return found
        if message["type"] == kind:
            found.append(message)


# --- the handshake ---


async def test_hello_after_a_good_handshake(live):
    client = await live.session()
    async with live.stream(client) as ws:
        hello = await next_of(ws, "hello")
    assert hello == {
        "type": "hello",
        "user": "ayush",
        "accounts": ["acc-ayush"],
        "flush_ms": 50,
        "max_instruments": 200,
    }
    await client.aclose()


async def test_handshake_from_another_site_or_without_a_session_is_refused(live):
    client = await live.session()
    cookie = {"Cookie": f"mt_session={client.cookies['mt_session']}"}
    url = f"ws://127.0.0.1:{live.port}/ws"
    with pytest.raises(InvalidStatus):
        async with connect(url, origin="https://evil.example", additional_headers=cookie):
            pass
    with pytest.raises(InvalidStatus):
        async with connect(url, origin=live.origin):
            pass
    await client.aclose()


# --- prices ---


async def test_prices_are_conflated_newest_wins(live):
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT]}))
        first = await next_of(ws, "ticks")
        assert [t["ltp_paise"] for t in first["ticks"]] == [2501000]  # current price at once
        for ltp in (2501010, 2501020, 2501030):  # three ticks inside one 50 ms window
            live.md.on_tick(Tick(FUT, ltp))
        batch = await next_of(ws, "ticks")
        assert [t["ltp_paise"] for t in batch["ticks"]] == [2501030]
        await ws.send(json.dumps({"op": "unsubscribe", "instruments": [FUT]}))
        await asyncio.sleep(0.1)
        live.md.on_tick(Tick(FUT, 2501040))
        assert await all_of(ws, "ticks") == []
    await client.aclose()


async def test_a_price_change_after_a_quiet_spell_goes_out_at_once(live):
    """No added latency: with the real 250 ms interval, a lone tick doesn't wait for a timer."""
    live.hub.flush_s = 0.25
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT]}))
        await next_of(ws, "ticks")  # the current price
        delays = []
        for n in range(5):
            await asyncio.sleep(0.3)  # quiet: longer than the interval, like Kite's ~1 tick/s
            sent = time.perf_counter()
            live.md.on_tick(Tick(FUT, 2501010 + 10 * n))
            batch = await next_of(ws, "ticks")
            delays.append(time.perf_counter() - sent)
            assert batch["ticks"][0]["ltp_paise"] == 2501010 + 10 * n
    assert max(delays) < 0.05, delays  # a fixed 250 ms timer would average ~125 ms
    await client.aclose()


async def test_a_burst_still_sends_at_most_one_update_per_interval(live):
    live.hub.flush_s = 0.25
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT]}))
        await next_of(ws, "ticks")
        start = time.perf_counter()
        for n in range(30):  # 30 ticks over ~0.6 s
            live.md.on_tick(Tick(FUT, 2502000 + 10 * n))
            await asyncio.sleep(0.02)
        batches = await all_of(ws, "ticks", quiet=0.4)
        elapsed = time.perf_counter() - start
    assert 2 <= len(batches) <= int(elapsed / 0.25) + 2  # throttled, not one per tick
    assert batches[-1]["ticks"][0]["ltp_paise"] == 2502290  # the newest always arrives
    await client.aclose()


async def test_subscription_mistakes_get_clear_errors(live):
    client = await live.session()
    live.hub._max_instruments = 1
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT, "NSE:NOPE"]}))
        assert (await next_of(ws, "error"))["message"] == "unknown instruments: ['NSE:NOPE']"
        await ws.send(json.dumps({"op": "subscribe", "instruments": ["NFO:NIFTY26OCTFUT", 7]}))
        assert "list of ids" in (await next_of(ws, "error"))["message"]
        await ws.send("not json")
        assert (await next_of(ws, "error"))["message"] == "unknown message"
        await ws.send(json.dumps({"op": "ping"}))
        assert await next_of(ws, "pong") == {"type": "pong"}
    await client.aclose()


async def test_instrument_limit_per_connection(live, nifty_fut):
    from dataclasses import replace

    live.store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT"))
    live.md.on_tick(Tick("NFO:NIFTY26NOVFUT", 2512000))  # has a price: would show if allowed
    live.hub._max_instruments = 1
    client = await live.session()
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT, "NFO:NIFTY26NOVFUT"]}))
        assert "at most 1 instruments" in (await next_of(ws, "error"))["message"]
        batch = await next_of(ws, "ticks")
        assert [t["instrument_id"] for t in batch["ticks"]] == [FUT]
    await client.aclose()


# --- orders ---


async def test_every_order_update_arrives_in_order(live):
    client = await live.session()
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        placed = (await client.post("/api/accounts/acc-ayush/orders", json=ORDER)).json()
        cid = placed["request"]["client_order_id"]
        orders = live.platform.accounts["acc-ayush"].orders
        for update in (
            OrderUpdate(cid, "F1", OrderStatus.OPEN),
            OrderUpdate(cid, "F1", OrderStatus.PARTIAL, 25, 2501000, 25, 2501000),
            OrderUpdate(cid, "F1", OrderStatus.PARTIAL, 50, 2501000, 25, 2501000),
            OrderUpdate(cid, "F1", OrderStatus.FILLED, 75, 2501000, 25, 2501000),
        ):
            orders.apply(update)  # all within a millisecond: none may be merged
        messages = await all_of(ws, "order")
    kinds = [(m["kind"], m["order"]["filled_qty"]) for m in messages]
    assert kinds == [
        ("SUBMITTED", 0),
        ("SENT", 0),
        ("UPDATED", 0),
        ("UPDATED", 25),
        ("FILL", 25),
        ("UPDATED", 50),
        ("FILL", 50),
        ("UPDATED", 75),
        ("FILL", 75),
    ]
    await client.aclose()


async def test_nobody_sees_other_peoples_orders(live):
    ayush = await live.session("ayush")
    riya = await live.session("riya")
    async with live.stream(riya) as ws:
        await next_of(ws, "hello")
        await ayush.post("/api/accounts/acc-ayush/orders", json=ORDER)
        assert await all_of(ws, "order") == []
    await ayush.aclose()
    await riya.aclose()


# --- positions and kill switches ---


async def test_positions_are_conflated_with_the_days_pnl(live):
    client = await live.session()
    book = live.platform.accounts["acc-ayush"].positions
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        for _ in range(3):
            book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
        live.platform.mark(Tick(FUT, 2501100))
        batches = await all_of(ws, "positions")
    assert len(batches) == 1  # four changes, one message
    (position,) = batches[0]["positions"]
    assert (position["qty"], position["unrealised_paise"]) == (225, 22500)
    assert batches[0]["day_pnl_paise"] == 22500
    await client.aclose()


async def test_algo_changes_are_pushed_in_order(live):
    client = await live.session()
    algos = live.platform.accounts["acc-ayush"].algos
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        algo = algos.start(
            "SLICER", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=150,
            params={"slices": 2, "interval_s": 60},
        )  # fmt: skip
        await algos.settle()
        algos.stop(algo.algo_id, "done")
        messages = await all_of(ws, "algo")
    kinds = [m["kind"] for m in messages]
    assert kinds[0] == "STARTED" and "STOPPING" in kinds
    assert messages[0]["algo"]["algo_id"] == algo.algo_id
    assert messages[-1]["algo"]["reason"] == "done"
    await client.aclose()


async def test_kill_switch_changes_are_pushed(live):
    client = await live.session()
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        await client.post("/api/accounts/acc-ayush/stop", json={"reason": "taking a break"})
        kill = await next_of(ws, "kill")
    assert (kill["scope"], kill["engaged"], kill["reason"], kill["by"]) == (
        "account acc-ayush",
        True,
        "taking a break",
        "ayush",
    )
    await client.aclose()


# --- safety ---


async def test_a_browser_that_cant_keep_up_is_disconnected_not_skipped(live):
    live.hub._queue_limit = 5
    client = await live.session()
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        switch = live.platform.accounts["acc-ayush"].switch
        for i in range(10):  # ten events with no chance to send in between
            switch.engage(f"r{i}", by="x") if i % 2 == 0 else switch.reset(by="x")
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                await asyncio.wait_for(ws.recv(), 2)
    assert closed.value.rcvd.code == CLOSE_TOO_SLOW
    await client.aclose()


async def test_logging_out_closes_the_stream(live):
    live.hub._recheck_s = 0.05
    client = await live.session()
    async with live.stream(client) as ws:
        await next_of(ws, "hello")
        await client.delete("/api/session")
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                await asyncio.wait_for(ws.recv(), 2)
    assert closed.value.rcvd.code == CLOSE_NOT_LOGGED_IN
    assert live.hub.connections == 0
    await client.aclose()


async def test_connections_are_cleaned_up_when_the_tab_closes(live):
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT]}))
        await next_of(ws, "ticks")
        assert live.hub.connections == 1 and FUT in live.md.watched()
    for _ in range(50):
        if live.hub.connections == 0:
            break
        await asyncio.sleep(0.02)
    assert live.hub.connections == 0 and FUT not in live.md.watched()
    await client.aclose()


def test_a_tick_is_encoded_once_with_the_same_fields_as_before():
    import json
    from datetime import UTC, datetime

    from mocktrade.domain.ticks import DepthLevel, Tick
    from mocktrade.server import stream

    t = Tick(
        "NFO:NIFTY26OCTFUT",
        2501000,
        volume=12345,
        exchange_time=datetime(2026, 10, 14, 4, 0, 1, 250000, tzinfo=UTC),
        bids=(DepthLevel(2500990, 150, 2),),
        asks=(DepthLevel(2501010, 225, 3),),
    )
    text = stream.tick_json(t)
    assert json.loads(text) == {
        "instrument_id": "NFO:NIFTY26OCTFUT",
        "ltp_paise": 2501000,
        "last_qty": None,
        "volume": 12345,
        "avg_price_paise": None,
        "buy_qty": None,
        "sell_qty": None,
        "open_paise": None,
        "high_paise": None,
        "low_paise": None,
        "close_paise": None,
        "open_interest": None,
        "exchange_time": "2026-10-14T04:00:01.250000+00:00",
        "bids": [[2500990, 150, 2]],
        "asks": [[2501010, 225, 3]],
    }
    assert stream.tick_json(t) is text  # the same tick: encoded once, shared
    newer = t._replace(ltp_paise=2501010)
    assert json.loads(stream.tick_json(newer))["ltp_paise"] == 2501010


async def test_ops_alerts_reach_every_screen_and_new_screens_see_the_active_ones(live):
    live.alerts.raise_("broker_login:acc-ayush", "WARN", "Log in to Kite for acc-ayush")
    client = await live.session()
    async with live.stream(client) as ws:
        first = await next_of(ws, "ops")  # active when the screen connected
        assert (first["key"], first["level"], first["active"]) == (
            "broker_login:acc-ayush",
            "WARN",
            True,
        )
        live.alerts.raise_("prices", "ERROR", "No prices for over a minute")
        assert (await next_of(ws, "ops"))["key"] == "prices"
        live.alerts.clear("prices", "prices are flowing again")
        cleared = await next_of(ws, "ops")
        assert (cleared["key"], cleared["level"], cleared["active"]) == ("prices", "OK", False)
    await client.aclose()


# --- option chains ---


def _add_chain(live):
    from datetime import date

    from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind

    exp = date(2026, 10, 27)
    for k in (24900, 25000, 25100):
        for kind in (InstrumentKind.CALL, InstrumentKind.PUT):
            live.store.add(
                Instrument(
                    Exchange.NFO, f"NIFTY26OCT{k}{kind.value}", kind, 5, 75, exp, k * 100, "NIFTY"
                )
            )
    return exp.isoformat()


async def test_an_option_chain_streams_at_once_then_at_most_once_per_interval(live):
    expiry = _add_chain(live)
    live.hub.chain_interval_s = 0.2
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(
            json.dumps({"op": "chain", "underlying": "NIFTY", "expiry": expiry, "strikes": 1})
        )
        first = await next_of(ws, "chain")
        assert first["underlying"] == "NIFTY" and first["atm_strike_paise"] == 2500000
        assert [r["strike_paise"] for r in first["rows"]] == [2490000, 2500000, 2510000]
        for n in range(30):  # a burst of prices for 0.6 s
            live.md.on_tick(Tick(FUT, 2501000 + 10 * n))
            await asyncio.sleep(0.02)
        updates = await all_of(ws, "chain", quiet=0.5)
        assert 2 <= len(updates) <= 5  # throttled, and the newest arrives
        assert updates[-1]["forward_paise"] == 2501290
        await ws.send(json.dumps({"op": "chain_stop", "underlying": "NIFTY", "expiry": expiry}))
        await asyncio.sleep(0.1)
        live.md.on_tick(Tick(FUT, 2502000))
        assert await all_of(ws, "chain", quiet=0.5) == []
    await client.aclose()


async def test_chain_requests_are_checked(live):
    expiry = _add_chain(live)
    client = await live.session()
    async with live.stream(client) as ws:
        await ws.send(json.dumps({"op": "chain", "underlying": "NIFTY", "expiry": "2030-01-01"}))
        assert "no options on NIFTY" in (await next_of(ws, "error"))["message"]
        await ws.send(json.dumps({"op": "chain", "underlying": "NIFTY", "expiry": "soon"}))
        assert "expiry date" in (await next_of(ws, "error"))["message"]
        for under in ("NIFTY", "NIFTY"):  # the same chain twice replaces, it doesn't add
            await ws.send(json.dumps({"op": "chain", "underlying": under, "expiry": expiry}))
        await next_of(ws, "chain")
    r = await client.get(f"/api/options/NIFTY/{expiry}/chain", params={"strikes": 1})
    assert r.status_code == 200 and len(r.json()["rows"]) == 3
    assert (await client.get("/api/options/NIFTY/expiries")).json() == [expiry]
    assert (await client.get("/api/options")).json() == ["NIFTY"]
    assert (await client.get("/api/options/NIFTY/2030-01-01/chain")).status_code == 404
    assert (await client.get("/api/options/NIFTY/soon/chain")).status_code == 422
    await client.aclose()
