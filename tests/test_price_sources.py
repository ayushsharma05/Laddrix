"""Each trader trades on and sees only their own subscription's prices (#395, #278).

Kite account A's ticker ─► A's prices ─► A's risk checks, positions, algos; A's owner's screens
Kite account B's ticker ─► B's prices ─► B's ...                ; B's owner's screens
a paper account          ─► its owner's own Kite prices
someone with no subscription of their own ─► no prices (on a Kite server)
"""

import asyncio

import pytest
from kite_packets import NFO_TOKEN
from test_gtt import CALL
from test_kite_live import AYUSH, OTHER, FakeTicker, settle, world  # noqa: F401 (fixture)

from laddrix.broker.fake import FakeBroker
from laddrix.broker.kite.decoder import KiteTick
from laddrix.domain.accounts import BrokerAccount, Role, User
from laddrix.domain.orders import OrderRequest, OrderType, Product, Side
from laddrix.domain.ticks import Tick
from laddrix.marketdata.service import MarketData
from laddrix.marketdata.sources import PriceSources
from laddrix.orders.model import ClientIds
from laddrix.platform import Platform
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.kill_switch import KillSwitch

FUT = "NFO:NIFTY26OCTFUT"


def test_the_rules_of_who_trades_and_sees_which_prices():
    shared, a, b = MarketData(), MarketData(), MarketData()
    sim = PriceSources(shared)
    assert sim.of("anything") is shared  # the simulator: Laddrix's own data, everyone's

    kite = PriceSources(shared, per_account=True)
    kite.register("acc-a", a)
    kite.register("acc-b", b)
    kite.borrow("paper-a", "acc-a")
    assert (kite.of("acc-a"), kite.of("acc-b"), kite.of("paper-a")) == (a, b, a)
    with pytest.raises(LookupError, match="no prices of its own"):
        kite.of("acc-c")  # never someone else's, never the shared feed
    assert {id(x) for x in kite.all()} == {id(a), id(b)}

    accounts = [BrokerAccount("acc-a", "kite", "AB", "ayush", frozenset({"riya"})),
                BrokerAccount("acc-b", "kite", "CD", "other")]  # fmt: skip
    ayush = User("ayush", "Ayush", frozenset({Role.TRADER}))
    riya = User("riya", "Riya", frozenset({Role.TRADER}))  # trades acc-a, owns nothing
    risk = User("risk", "Risk", frozenset({Role.RISK}))
    other_firm = User("ayush2", "A2", frozenset({Role.TRADER}), workspace="acme")
    assert kite.for_user(ayush, accounts) is a
    assert kite.for_user(riya, accounts) is None  # not her subscription (until Zerodha says)
    assert kite.for_user(risk, accounts) is None
    assert kite.for_user(other_firm, accounts) is None
    assert sim.for_user(riya, accounts) is shared


async def test_each_account_is_checked_and_marked_on_its_own_prices(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    shared, a, b = MarketData(), MarketData(), MarketData()
    a.on_tick(Tick(FUT, 2501000))
    b.on_tick(Tick(FUT, 3000000))  # the other subscription: a very different price
    prices = PriceSources(shared, per_account=True)
    prices.register("acc-a", a)
    prices.register("acc-b", b)
    platform = Platform(store, shared, KillSwitch("firm"), price_sources=prices)
    runs = {acc: platform.add_account(BrokerAccount(acc, "kite", acc, "u"), FakeBroker(),
                                      ids=ClientIds(acc[-1].upper()))
            for acc in ("acc-a", "acc-b")}  # fmt: skip

    def buy(account, paise):
        return OrderRequest("", account, "u", FUT, Side.BUY, 75, OrderType.LIMIT, Product.NRML,
                            price_paise=paise)  # fmt: skip

    # the price band is judged on each account's own last price
    near_a = await runs["acc-a"].orders.submit(buy("acc-a", 2501000))
    near_b_on_a = await runs["acc-a"].orders.submit(buy("acc-a", 3000000))
    near_b = await runs["acc-b"].orders.submit(buy("acc-b", 3000000))
    assert near_a.status.value != "REJECTED" and near_b.status.value != "REJECTED"
    assert near_b_on_a.status.value == "REJECTED" and "last price ₹25,010.00" in near_b_on_a.reason

    # positions are marked from their own feed
    runs["acc-a"].positions.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    runs["acc-b"].positions.apply_fill(FUT, Product.NRML, Side.BUY, 75, 3000000)
    task = asyncio.create_task(platform._mark_loop())
    try:
        await asyncio.sleep(0.05)
        a.on_tick(Tick(FUT, 2502000))
        b.on_tick(Tick(FUT, 3005000))
        async with asyncio.timeout(3):

            def ltp(account):
                return runs[account].positions.positions()[0].ltp_paise

            while not (ltp("acc-a") == 2502000 and ltp("acc-b") == 3005000):
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_each_kite_accounts_ticker_feeds_only_its_own_prices(world):  # noqa: F811
    world.live.add_account(AYUSH)
    world.live.add_account(OTHER)
    mine, theirs = world.live.prices_of(AYUSH.id), world.live.prices_of(OTHER.id)
    assert mine is not theirs
    await world.live.start()
    await world.live.login.complete(AYUSH, "req-a")
    await world.live.login.complete(OTHER, "req-b")
    await settle()
    first, second = FakeTicker.made
    mine.subscribe([FUT])  # A's screens open the future: only A's ticker subscribes it
    await settle()
    assert NFO_TOKEN in first.subscribed and NFO_TOKEN not in second.subscribed
    first.on_ticks([KiteTick(token=NFO_TOKEN, mode="ltp", ltp_paise=2501000)])
    second.on_ticks([KiteTick(token=NFO_TOKEN, mode="ltp", ltp_paise=2500000)])
    assert mine.latest(FUT).ltp_paise == 2501000  # A's own subscription's price
    assert theirs.latest(FUT).ltp_paise == 2500000  # B's own
    await world.live.aclose()


async def test_a_screen_gets_ticks_only_from_its_own_subscription(tmp_path, nifty_fut):
    """A live server on 'Kite' prices: two owners, two feeds, a delegated trader with none."""
    import json

    from test_stream import Live, next_of

    server = Live(tmp_path, nifty_fut)
    a, b = MarketData(), MarketData()
    server.platform.prices = PriceSources(server.md, per_account=True)
    server.platform.prices.register("acc-ayush", a)
    server.platform.prices.register("acc-riya", b)
    await server.start()
    try:
        ayush, riya = await server.session("ayush"), await server.session("riya")
        async with server.stream(ayush) as mine, server.stream(riya) as theirs:
            for ws in (mine, theirs):
                await next_of(ws, "hello")
                await ws.send(json.dumps({"op": "subscribe", "instruments": [FUT]}))
            await asyncio.sleep(0.2)
            a.on_tick(Tick(FUT, 2501000))
            b.on_tick(Tick(FUT, 2600000))
            got_mine = await next_of(mine, "ticks")
            got_theirs = await next_of(theirs, "ticks")
            assert {t["ltp_paise"] for t in got_mine["ticks"]} == {2501000}
            assert {t["ltp_paise"] for t in got_theirs["ticks"]} == {2600000}
            server.md.on_tick(Tick(FUT, 9999900))  # the shared feed: nobody's screens here
            a.on_tick(Tick(FUT, 2501500))
            again = await next_of(mine, "ticks")
            assert {t["ltp_paise"] for t in again["ticks"]} == {2501500}
    finally:
        await server.stop()


async def test_on_a_kite_server_a_paper_account_fills_on_its_owners_own_prices(tmp_path):
    from test_serve import KEY, env

    from laddrix.server.security import SecretBox
    from laddrix.server.users import UserStore
    from laddrix.tools.serve import ServeSettings, build

    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(KEY))
    for user in ("ayush", "riya"):
        users.create_user(user, user.title(), "a-long-password-1", {Role.TRADER})
    users.add_account(BrokerAccount("acc-kite", "kite", "AB1234", "ayush"))
    users.add_account(BrokerAccount("acc-paper", "paper", "P1", "ayush"))  # ayush's: his prices
    users.add_account(BrokerAccount("acc-p2", "paper", "P2", "riya"))  # riya has no Kite account
    settings = ServeSettings.from_env(env(LADDRIX_DATA=str(data), LADDRIX_PRICES="kite",
                                          KITE_API_KEY="k", KITE_API_SECRET="s"))  # fmt: skip
    built = build(settings)
    try:
        own = built.kite.prices_of("acc-kite")
        assert built.platform.prices.of("acc-kite") is own
        assert built.platform.prices.of("acc-paper") is own  # fills on its owner's prices
        assert built.platform.prices.of("acc-paper") is not built.platform.market_data
        assert sorted(built.running) == ["acc-kite", "acc-paper"]
        assert "its owner's own Kite prices" in built.not_running["acc-p2"]  # never another's
    finally:
        await built.close()


# --- the server-wide features: each person's own prices (#395, second half) ---------------------


def book(ltp, volume, bid, ask):
    from laddrix.domain.ticks import DepthLevel

    return Tick(FUT, ltp, volume=volume, bids=(DepthLevel(bid, 75, 1),),
                asks=(DepthLevel(ask, 75, 1),))  # fmt: skip


async def test_every_market_screen_reads_the_askers_own_subscription(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from laddrix.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)  # ayush owns acc-ayush, riya owns acc-riya
    a, b = MarketData(), MarketData()
    w.platform.prices = PriceSources(w.platform.market_data, per_account=True)
    w.platform.prices.register("acc-ayush", a)
    w.platform.prices.register("acc-riya", b)
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN))  # sees both sources
    ayush, riya, risk = [await w.logged_in(u) for u in ("ayush", "riya", "risk")]
    for source in (a, b):
        source.subscribe([FUT])  # a screen watches it: prints are kept
    for t in (book(2500000, 1000, 2499990, 2500010), book(2500010, 1075, 2500000, 2500020)):
        a.on_tick(t)
    b.on_tick(book(2600000, 5000, 2599990, 2600010))

    def ltp(c):
        return c.get(f"/api/instruments/{FUT}")

    assert (await ltp(ayush)).json()["ltp_paise"] == 2500010  # his own subscription's
    assert (await ltp(riya)).json()["ltp_paise"] == 2600000  # hers
    assert (await ltp(risk)).json()["ltp_paise"] is None  # no subscription of their own
    prints = (await ayush.get(f"/api/instruments/{FUT}/prints")).json()["prints"]
    assert [(p["qty"], p["side"]) for p in prints] == [(75, "BUY")]
    assert (await riya.get(f"/api/instruments/{FUT}/prints")).json()["prints"] == []
    movers = (await riya.get("/api/market/movers", params={"by": "volume"})).json()["rows"]
    assert movers[0]["volume"] == 5000  # her feed's volume, not his
    assert (await ayush.get(f"/api/instruments/{FUT}/vap")).json()["volume"] == 1075
    assert (await risk.get("/api/market/movers", params={"by": "volume"})).json()["rows"] == []


def test_a_price_alert_fires_only_on_its_owners_own_prices():
    from laddrix.alerts.engine import AlertEngine

    a, b = MarketData(), MarketData()
    owners = {"ayush": a, "riya": b}
    fired = []
    engine = AlertEngine(lambda i: None, lambda i: True, source_of=owners.get)
    engine.on_fire(fired.append)
    for user in ("ayush", "riya"):
        engine.create(user, {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                             "params": {"level_paise": 2550000}})  # fmt: skip
    for source in (a, b):
        source.tap(lambda tick, s=source: engine.on_tick(tick, s))
    a.on_tick(Tick(FUT, 2500000))
    b.on_tick(Tick(FUT, 2500000))  # both arm below the level
    b.on_tick(Tick(FUT, 2600000))  # only riya's subscription crosses
    assert [f.alert.user_id for f in fired] == ["riya"]
    # an alert made now is judged at once on its owner's own current price
    late = engine.create("riya", {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                  "params": {"level_paise": 2550000}})  # fmt: skip
    assert late.status.startswith("already true")  # her price (₹26,000) is past the level
    mine = engine.create("ayush", {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                   "params": {"level_paise": 2550000}})  # fmt: skip
    assert mine.status == "watching"  # his (₹25,000) is not
    assert engine.watched_for(a) == {FUT} and engine.watched_for(MarketData()) == set()


async def test_chart_history_goes_through_the_askers_own_login(world):  # noqa: F811
    world.live.add_account(AYUSH)
    world.live.add_account(OTHER)
    await world.live.start()
    await world.live.login.complete(AYUSH, "req-a")
    await world.live.login.complete(OTHER, "req-b")
    await settle()
    assert world.live.account_of(world.live.prices_of(OTHER.id)) == OTHER.id
    await world.live.reader(OTHER.id).request("GET", "/instruments/historical/1/minute")
    assert world.kite.calls[-1][2] == "tok-b"  # its own login, not the first account's
    world.live.log_out(world.live._links[OTHER.id], "test")
    from laddrix.broker.kite.http import KiteAuthError

    with pytest.raises(KiteAuthError, match="isn't logged in"):
        await world.live.reader(OTHER.id).request("GET", "/instruments/historical/1/minute")
    await world.live.aclose()


async def test_each_screens_forming_candle_is_built_from_its_own_ticks(tmp_path, nifty_fut):
    import json

    from test_charts import t, tick
    from test_stream import Live, all_of, next_of

    from laddrix.charts.candles import CandleBuilder
    from laddrix.charts.service import Charts
    from laddrix.server.app import ServerSettings, create_app

    server = Live(tmp_path, nifty_fut)
    server.store.add(CALL)  # a NIFTY option, for the chain
    a, b = MarketData(), MarketData()
    server.platform.prices = PriceSources(server.md, per_account=True)
    server.platform.prices.register("acc-ayush", a)
    server.platform.prices.register("acc-riya", b)

    def charts_for(source):  # as serve does on a Kite server: each source its own builder
        own = CandleBuilder()
        source.tap(own.on_tick)
        return Charts(own, None)

    server.app = create_app(server.platform, server.users,
                            ServerSettings(origin=server.origin, cookie_secure=False),
                            charts_for=charts_for)  # fmt: skip
    server.hub = server.app.state.hub
    server.hub.candle_interval_s = 0.1
    await server.start()
    try:
        ayush, riya = await server.session("ayush"), await server.session("riya")
        async with server.stream(ayush) as mine, server.stream(riya) as theirs:
            for ws in (mine, theirs):
                await ws.send(json.dumps({"op": "candles", "instrument_id": FUT,
                                          "interval": "5m"}))  # fmt: skip
                assert (await next_of(ws, "candle"))["candle"] is None
            a.on_tick(tick(t(9, 20, 1), 2501000, 1000))
            b.on_tick(tick(t(9, 20, 1), 2700000, 9000))
            got_mine = (await all_of(mine, "candle", quiet=0.4))[-1]["candle"]
            got_theirs = (await all_of(theirs, "candle", quiet=0.4))[-1]["candle"]
            assert got_mine["o"] == 2501000 and got_theirs["o"] == 2700000
            assert a.is_watched(FUT) and b.is_watched(FUT)  # each asks its own feed for it…
            assert not server.md.is_watched(FUT)  # …never the shared one

            # an option chain from each one's own prices
            a.on_tick(Tick(CALL.id, 12000))
            b.on_tick(Tick(CALL.id, 15000))
            for ws in (mine, theirs):
                await ws.send(json.dumps({"op": "chain", "underlying": "NIFTY",
                                          "expiry": "2026-10-27", "strikes": 5}))  # fmt: skip
            chain_mine = json.dumps(await next_of(mine, "chain"))
            chain_theirs = json.dumps(await next_of(theirs, "chain"))
            assert "12000" in chain_mine and "15000" not in chain_mine
            assert "15000" in chain_theirs and "12000" not in chain_theirs
    finally:
        await server.stop()
