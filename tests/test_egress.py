"""How each account's orders leave the server: its own IP or the shared one (#372).

SEBI wants API orders to come from a static IP registered with the broker. Whether many
traders may share the server's one IP, or each needs their own, is for Zerodha to confirm
(#278). Laddrix is ready for both: a setting per account, not a code change.

    KITE_EGRESS_<ACCOUNT> unset or "direct" ─► out from the server's static IP (shared)
    KITE_EGRESS_<ACCOUNT>=http://user:pw@host:port ─► out through that proxy (its own IP)
    the account's daily login leaves the same way as its orders
"""

import asyncio

import pytest

from laddrix.broker.kite.http import KiteHttp, http_for
from laddrix.config import ConfigError, KiteSettings
from laddrix.domain.accounts import BrokerAccount


class Proxy:
    """A tiny HTTP proxy: records each request it forwards and answers it like Kite."""

    def __init__(self):
        self.seen: list[str] = []
        self.server = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _serve(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        self.seen.append(head.split("\r\n")[0])  # e.g. "GET http://kite.test/user/profile HTTP/1.1"
        body = b'{"status": "success", "data": {"user_id": "AB1234"}}'
        head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        writer.write(head + b"Content-Length: %d\r\n\r\n" % len(body) + body)
        await writer.drain()
        writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


async def test_an_account_with_its_own_way_out_really_goes_through_it():
    proxy = Proxy()
    address = await proxy.start()
    try:
        app = KiteSettings("key", "secret", None, egress=address)  # type: ignore[arg-type]

        def factory(key, token=None, proxy=None):
            return KiteHttp(key, "token", base_url="http://kite.test", proxy=proxy)

        http = http_for(factory, app)
        assert await http.request("GET", "/user/profile") == {"user_id": "AB1234"}
        await http.aclose()
        assert proxy.seen == ["GET http://kite.test/user/profile HTTP/1.1"]
    finally:
        await proxy.stop()


def test_an_account_without_one_goes_out_directly():
    made = []

    def factory(key, token=None, **kw):
        made.append((key, kw))
        return object()

    http_for(factory, KiteSettings("k1", "s", None))  # type: ignore[arg-type]
    http_for(factory, KiteSettings("k2", "s", None, egress="http://p:3128"))  # type: ignore[arg-type]
    assert made == [("k1", {}), ("k2", {"proxy": "http://p:3128"})]


def test_the_setting_per_account_with_a_default_and_direct():
    from test_serve import env

    from laddrix.tools.serve import ServeSettings

    a = BrokerAccount("acc-a", "kite", "AB1", "ayush")
    b = BrokerAccount("acc-b", "kite", "CD2", "riya")
    c = BrokerAccount("acc-c", "kite", "EF3", "ravi")
    s = ServeSettings.from_env(env(KITE_API_KEY="k", KITE_API_SECRET="s",
                                   KITE_EGRESS="http://shared-proxy:3128",
                                   KITE_EGRESS_ACC_A="http://u:p@10.0.0.5:3128",
                                   KITE_EGRESS_ACC_C="direct"))  # fmt: skip
    assert s.kite_app(a).egress == "http://u:p@10.0.0.5:3128"  # its own IP
    assert s.kite_app(b).egress == "http://shared-proxy:3128"  # the default way out
    assert s.kite_app(c).egress is None  # the server's static IP
    plain = ServeSettings.from_env(env(KITE_API_KEY="k", KITE_API_SECRET="s"))
    assert plain.kite_app(a).egress is None  # nothing set: everyone shares the server's IP
    bad = ServeSettings.from_env(env(KITE_API_KEY="k", KITE_API_SECRET="s",
                                     KITE_EGRESS_ACC_A="ftp://x"))  # fmt: skip
    with pytest.raises(ConfigError, match="KITE_EGRESS_ACC_A must be 'direct' or a proxy"):
        bad.kite_app(a)


async def test_the_live_wiring_and_the_daily_login_use_each_accounts_way_out(tmp_path):
    from test_kite_live import AYUSH, OTHER, FakeTicker

    from laddrix.broker.kite.daily_login import KiteDailyLogin
    from laddrix.broker.kite.live import KiteLive
    from laddrix.marketdata.service import MarketData
    from laddrix.refdata.store import InstrumentStore

    made = []

    class Http:
        def __init__(self, key, token=None, proxy=None):
            made.append((key, proxy))

        async def aclose(self):
            pass

    def apps(account):
        own = "http://own-ip:3128" if account.id == AYUSH.id else None
        return KiteSettings(f"key-{account.id}", "s", tmp_path / "t", egress=own)

    live = KiteLive(apps, tmp_path, InstrumentStore(), MarketData(),
                    instruments_cache=tmp_path / "i.csv", http_factory=Http,
                    ticker_factory=FakeTicker)  # fmt: skip
    live.add_account(AYUSH)
    live.add_account(OTHER)
    assert made == [("key-acc-a", "http://own-ip:3128"), ("key-acc-b", None)]

    made.clear()
    login = KiteDailyLogin(apps, tmp_path, http_factory=Http)
    with pytest.raises(AttributeError):  # the fake can't log in: what matters is how it left
        await login.complete(AYUSH, "request-token")
    assert made == [("key-acc-a", "http://own-ip:3128")]
