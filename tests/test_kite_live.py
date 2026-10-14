"""Live Kite in the server, against a fake Kite (HTTP and ticker): login to 6 AM."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from kite_packets import NFO_TOKEN, NSE_TOKEN

from mocktrade.broker.base import BrokerError
from mocktrade.broker.kite import token_store
from mocktrade.broker.kite.daily_login import WrongKiteUser
from mocktrade.broker.kite.decoder import KiteTick
from mocktrade.broker.kite.http import KiteHttp
from mocktrade.broker.kite.live import LIVE, LOGIN_NEEDED, KiteLive
from mocktrade.broker.kite.session import KiteSession
from mocktrade.config import KiteSettings
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore

CSV = (Path(__file__).parent / "data" / "kite_instruments_sample.csv").read_text()
FUT, REL = "NFO:NIFTY26OCTFUT", "NSE:RELIANCE"
T0 = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)  # 08:30 in India
AYUSH = BrokerAccount("acc-a", "kite", "AB1234", "ayush")
OTHER = BrokerAccount("acc-b", "kite", "CD5678", "other")


class FakeKiteHttp:
    """Kite's REST side: login, the instrument dump, orders. Records every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []  # method, path, token
        self.users = {"req-a": ("AB1234", "tok-a"), "req-b": ("CD5678", "tok-b")}

    def handler(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        token = auth.split(":", 1)[1] if ":" in auth else None
        self.calls.append((request.method, request.url.path, token))
        path = request.url.path
        if path == "/session/token":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            user, tok = self.users[form["request_token"]]
            return ok({"user_id": user, "access_token": tok})
        if token is None:
            return httpx.Response(403, json={"status": "error", "error_type": "TokenException"})
        if path == "/instruments":
            return httpx.Response(200, text=CSV)
        if path.startswith("/orders/"):
            return ok({"order_id": "K1"})
        if path == "/orders":
            return ok([])
        return httpx.Response(404, json={"status": "error", "message": "no"})

    def factory(self, api_key, access_token=None):
        return KiteHttp(
            api_key, access_token, transport=httpx.MockTransport(lambda r: self.handler(r))
        )


def ok(data):
    return httpx.Response(200, json={"status": "success", "data": data})


class FakeTicker:
    made: list["FakeTicker"] = []

    def __init__(self, api_key, access_token, on_ticks, on_order=None, *, on_connect=None):
        self.access_token = access_token
        self.on_ticks, self.on_order, self.on_connect = on_ticks, on_order, on_connect
        self.subscribed: dict[int, str] = {}
        self.stopped = False
        self.fail: Exception | None = None
        self._done = asyncio.Event()
        FakeTicker.made.append(self)

    async def subscribe(self, tokens, mode="full"):
        for t in tokens:
            self.subscribed[int(t)] = mode

    def stop(self):
        self.stopped = True
        self._done.set()

    async def run(self):
        if self.on_connect:
            self.on_connect()
        await self._done.wait()
        if self.fail:
            raise self.fail


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path):
    FakeTicker.made = []
    kite = FakeKiteHttp()
    store, md, clock = InstrumentStore(), MarketData(), Clock()
    reconnects: list[str] = []
    live = KiteLive(
        lambda account: KiteSettings(f"key-{account.id}", "secret", tmp_path / "unused"),
        tmp_path,
        store,
        md,
        instruments_cache=tmp_path / "kite_instruments.csv",
        on_reconnect=reconnects.append,
        http_factory=kite.factory,
        ticker_factory=FakeTicker,
        clock=clock,
    )

    class W:
        pass

    w = W()
    w.kite, w.store, w.md, w.clock, w.live, w.tmp, w.reconnects = (
        kite,
        store,
        md,
        clock,
        live,
        tmp_path,
        reconnects,
    )
    return w


def request(cid="MT1"):
    return OrderRequest(
        cid, "acc-a", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT, Product.NRML, price_paise=2501000
    )


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_before_login_orders_are_refused_and_the_screen_is_told(world):
    broker = world.live.add_account(AYUSH)
    await world.live.start()
    assert world.live.status(AYUSH) == LOGIN_NEEDED
    with pytest.raises(BrokerError, match="Kite login needed"):
        await broker.place(request())
    assert world.live.prices_from is None and FakeTicker.made == []


async def test_login_from_the_screen_goes_live_without_a_restart(world):
    broker = world.live.add_account(AYUSH)
    await world.live.start()
    watching = world.md.subscribe([FUT])  # a ladder was open before the login
    await world.live.login.complete(AYUSH, "req-a")
    await settle()
    assert world.live.status(AYUSH) == LIVE and world.live.prices_from == "acc-a"
    # today's instruments were downloaded and cached
    assert world.store.get(FUT) is not None
    assert (world.tmp / "kite_instruments.csv").read_text() == CSV
    assert (world.tmp / "kite_token_acc-a.json").exists()
    (ticker,) = FakeTicker.made
    assert ticker.access_token == "tok-a"
    assert ticker.subscribed == {NFO_TOKEN: "full"}  # what was watched before the login
    assert world.reconnects == ["acc-a"]  # resync on (re)connect
    # a contract opened now is subscribed at once
    world.md.subscribe([REL])
    await settle()
    assert NSE_TOKEN in ticker.subscribed
    # ticks reach market data; orders go out with the token
    ticker.on_ticks([KiteTick(token=NFO_TOKEN, mode="ltp", ltp_paise=2501000)])
    assert world.md.latest(FUT).ltp_paise == 2501000
    assert await broker.place(request()) == "K1"
    assert ("POST", "/orders/regular", "tok-a") in world.kite.calls
    watching.close()
    await world.live.aclose()


async def test_order_updates_from_the_ticker_reach_the_accounts_broker(world):
    broker = world.live.add_account(AYUSH)
    await world.live.start()
    await world.live.login.complete(AYUSH, "req-a")
    await settle()
    message = json.loads(
        json.dumps(
            {
                "user_id": "AB1234",
                "order_id": "K1",
                "tag": "MT1",
                "status": "OPEN",
                "variety": "regular",
                "filled_quantity": 0,
                "average_price": 0,
                "order_timestamp": "2026-10-14 08:31:00",
            }
        )
    )
    FakeTicker.made[0].on_order(message)
    update = await asyncio.wait_for(anext(aiter(broker.updates())), 1)
    assert (update.client_order_id, update.broker_order_id) == ("MT1", "K1")
    await world.live.aclose()


async def test_logging_in_as_another_zerodha_client_is_refused(world):
    world.live.add_account(AYUSH)
    await world.live.start()
    with pytest.raises(WrongKiteUser):
        await world.live.login.complete(AYUSH, "req-b")
    assert world.live.status(AYUSH) == LOGIN_NEEDED and FakeTicker.made == []


async def test_a_restart_during_the_day_uses_the_saved_login_and_the_cached_list(world):
    world.live.add_account(AYUSH)
    (world.tmp / "kite_instruments.csv").write_text(CSV)
    token_store.save(world.tmp / "kite_token_acc-a.json", KiteSession("AB1234", "tok-a"), T0)
    world.clock.now = T0 + timedelta(hours=2)
    await world.live.start()
    assert world.live.status(AYUSH) == LIVE
    assert FakeTicker.made[0].access_token == "tok-a"
    await world.live.aclose()


async def test_yesterdays_login_is_not_used(world):
    world.live.add_account(AYUSH)
    token_store.save(world.tmp / "kite_token_acc-a.json", KiteSession("AB1234", "tok-a"), T0)
    world.clock.now = T0 + timedelta(days=1)  # past 6 AM
    await world.live.start()
    assert world.live.status(AYUSH) == LOGIN_NEEDED


async def test_at_6_am_orders_stop_and_prices_move_to_another_live_account(world):
    a = world.live.add_account(AYUSH)
    world.live.add_account(OTHER)
    await world.live.start()
    world.md.subscribe([FUT])
    await world.live.login.complete(AYUSH, "req-a")
    world.clock.now = T0 + timedelta(hours=1)
    await world.live.login.complete(OTHER, "req-b")  # logged in later: expires at the same 6 AM
    await settle()
    first, second = FakeTicker.made
    assert world.live.prices_from == "acc-a" and second.subscribed == {}
    # AYUSH's connection is refused (e.g. token revoked): prices move to OTHER
    first.fail = RuntimeError("HTTP 403")
    first._done.set()
    await settle()
    assert world.live.status(AYUSH) == LOGIN_NEEDED
    assert world.live.prices_from == "acc-b" and second.subscribed == {NFO_TOKEN: "full"}
    with pytest.raises(BrokerError, match="Kite login needed"):
        await a.place(request())
    # 6 AM: everyone is logged out
    world.clock.now = datetime(2026, 10, 15, 0, 30, tzinfo=UTC)
    world.live.expire_due()
    await settle()
    assert world.live.status(OTHER) == LOGIN_NEEDED and world.live.prices_from is None
    assert second.stopped
    await world.live.aclose()


async def test_a_failed_instrument_download_keeps_the_cached_list(world):
    world.live.add_account(AYUSH)
    (world.tmp / "kite_instruments.csv").write_text(CSV)
    await world.live.start()
    world.kite.handler_orig = world.kite.handler

    def broken(request):
        if request.url.path == "/instruments":
            return httpx.Response(500, json={"status": "error", "message": "busy"})
        return world.kite.handler_orig(request)

    world.kite.handler = broken
    await world.live.login.complete(AYUSH, "req-a")
    assert world.live.status(AYUSH) == LIVE  # still trades on yesterday's list
    assert world.store.get(FUT) is not None
    assert (world.tmp / "kite_instruments.csv").read_text() == CSV
    await world.live.aclose()


async def test_one_connection_carries_prices_and_the_list_downloads_once_a_day(world):
    world.live.add_account(AYUSH)
    world.live.add_account(OTHER)
    await world.live.start()
    await world.live.login.complete(AYUSH, "req-a")
    await world.live.login.complete(OTHER, "req-b")
    await settle()
    first, second = FakeTicker.made
    second.on_ticks([KiteTick(token=NFO_TOKEN, mode="ltp", ltp_paise=2500000)])
    assert world.md.ticks_received == 0  # a second copy would double volume-at-price
    first.on_ticks([KiteTick(token=NFO_TOKEN, mode="ltp", ltp_paise=2501000)])
    assert world.md.ticks_received == 1
    downloads = [c for c in world.kite.calls if c[1] == "/instruments"]
    assert len(downloads) == 1  # the second login reused today's list
    await world.live.aclose()
