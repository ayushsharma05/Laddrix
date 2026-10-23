"""Each trader trades on and sees only their own subscription's prices (#395, #278).

Kite account A's ticker ─► A's prices ─► A's risk checks, positions, algos; A's owner's screens
Kite account B's ticker ─► B's prices ─► B's ...                ; B's owner's screens
a paper account          ─► its owner's own Kite prices
someone with no subscription of their own ─► no prices (on a Kite server)
"""

import asyncio

import pytest
from kite_packets import NFO_TOKEN
from test_kite_live import AYUSH, OTHER, FakeTicker, settle, world  # noqa: F401 (fixture)

from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.kite.decoder import KiteTick
from mocktrade.domain.accounts import BrokerAccount, Role, User
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sources import PriceSources
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch

FUT = "NFO:NIFTY26OCTFUT"


def test_the_rules_of_who_trades_and_sees_which_prices():
    shared, a, b = MarketData(), MarketData(), MarketData()
    sim = PriceSources(shared)
    assert sim.of("anything") is shared  # the simulator: MockTrade's own data, everyone's

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
    platform = Platform(store, shared, KillSwitch("firm"), prices=prices)
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

    from mocktrade.server.security import SecretBox
    from mocktrade.server.users import UserStore
    from mocktrade.tools.serve import ServeSettings, build

    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(KEY))
    for user in ("ayush", "riya"):
        users.create_user(user, user.title(), "a-long-password-1", {Role.TRADER})
    users.add_account(BrokerAccount("acc-kite", "kite", "AB1234", "ayush"))
    users.add_account(BrokerAccount("acc-paper", "paper", "P1", "ayush"))  # ayush's: his prices
    users.add_account(BrokerAccount("acc-p2", "paper", "P2", "riya"))  # riya has no Kite account
    settings = ServeSettings.from_env(env(MOCKTRADE_DATA=str(data), MOCKTRADE_PRICES="kite",
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
