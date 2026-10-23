"""Files and jobs per firm and per subscription (#396, docs/design/tenancy.md §3, §5).

recorded ticks    one folder per Kite subscription: data/ticks/@<account>   (the simulator:
                  data/ticks, MockTrade's own data, everyone's)
imported candles  one folder per firm: data/candles (main), data/candles/@<workspace>
a backtest        replays its owner's own subscription's days and their firm's candles
reports           marked at the account's own prices; one folder per account
the price checks  each subscription checked for silence on its own
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from test_backtest import DAY, MODEL, T0, day_ticks, spec, store  # noqa: F401 (fixture)
from test_server import FUT, ORIGIN
from test_server import World as ServerWorld
from test_ticks_recorder import full
from test_workspaces import ACME, TwoFirms

from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.marketdata.sources import PriceSources
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.checks import PRICES_SILENT_S, Checks, PriceFeed, PriceOverload
from mocktrade.orders.model import EventKind, Fill, OrderEvent, OrderView, Pending
from mocktrade.reports.service import Reports
from mocktrade.research.backtest import BacktestError, Backtests, CandleStores
from mocktrade.research.ticks import FileTickStore
from mocktrade.server.app import ServerSettings, create_app

CANDLES = [{"time": (T0 + timedelta(minutes=m)).isoformat(), "open": 2500000, "high": 2500300,
            "low": 2499800, "close": 2500200} for m in range(5)]  # fmt: skip


def test_each_firms_candles_live_in_its_own_folder(tmp_path):
    stores = CandleStores(tmp_path / "candles")
    assert stores.of("main").root == tmp_path / "candles"  # as before #368: nothing moves
    acme = stores.of(ACME)
    assert acme.root == tmp_path / "candles" / "@acme"
    acme.put(FUT, DAY, CANDLES)
    assert acme.days(FUT) == [DAY.isoformat()]
    assert stores.of("main").days(FUT) == []  # acme's import is acme's alone
    assert stores.of("other").rows(FUT, DAY) is None
    for bad in ("../main", "a/b", "", "ac me"):
        with pytest.raises(BacktestError, match="not a workspace id"):
            stores.of(bad)


async def test_a_backtest_replays_only_its_owners_own_data(nifty_fut, store, tmp_path):  # noqa: F811
    fut = nifty_fut.id
    mine = {DAY: day_ticks(fut)}
    stores = CandleStores(tmp_path / "candles")
    stores.of(ACME).put(fut, DAY, CANDLES)

    def sources_of(owner):  # as serve does: the owner's own recordings, their firm's candles
        ticks = (lambda d: iter(mine[d]) if d in mine else None) if owner == "ravi" else (
            lambda _d: None)  # fmt: skip
        return ticks, stores.of(ACME if owner == "ravi" else "main").rows

    runs = Backtests(tmp_path / "runs", store, MODEL, ticks=lambda _d: None, sources_of=sources_of)
    for candles in (False, True):
        theirs = runs.start(spec(fut), candles=candles, owner="ravi")
        ours = runs.start(spec(fut), candles=candles, owner="ayush")
        await runs.settle()
        assert theirs.result["summary"]["days_skipped"] == []  # ravi's own day / acme's candles
        assert ours.result["summary"]["days_skipped"] == [DAY.isoformat()]  # not ours to read

    def no_candles(_owner):
        return (lambda _d: None), None

    none = Backtests(tmp_path / "none", store, MODEL, ticks=lambda _d: None, sources_of=no_candles)
    with pytest.raises(BacktestError, match="no candles were imported"):
        none.start(spec(fut), candles=True, owner="ravi")


async def test_a_firms_admin_imports_candles_for_their_own_firm_only(tmp_path, nifty_fut):
    w = TwoFirms(tmp_path, nifty_fut)
    stores = CandleStores(tmp_path / "candles")
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), candles_for=stores.of,
                       backtests=Backtests(tmp_path / "runs", w.platform.instruments, MODEL,
                                           ticks=lambda _d: None))  # fmt: skip
    ravi, tara, ayush = [await w.logged_in(u) for u in ("ravi", "tara", "ayush")]
    body = {"instrument_id": FUT, "day": DAY.isoformat(), "rows": CANDLES}
    r = await ravi.post("/api/research/candles", json=body)
    assert r.status_code == 201, r.text  # two firms on the server, yet no operator needed
    assert (await tara.post("/api/research/candles", json=body)).status_code == 403  # an admin's

    def candle_days(c):
        return c.get("/api/research/backtests/form", params={"instrument_id": FUT})

    assert (await candle_days(ravi)).json()["candle_days"] == [DAY.isoformat()]
    assert (await candle_days(tara)).json()["candle_days"] == [DAY.isoformat()]  # same firm
    assert (await candle_days(ayush)).json()["candle_days"] == []  # another firm's data
    assert stores.of("main").days(FUT) == [] and stores.of(ACME).days(FUT) == [DAY.isoformat()]
    await w.log.flush()
    assert w.log.notes("-", ["CANDLES_IMPORTED"])[0].extra["by"] == "ravi"

    # one shared dataset (no per-firm stores): with two firms only the operator may change it
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN),
                       candles=stores.of("main"))  # fmt: skip
    ravi = await w.logged_in("ravi")
    r = await ravi.post("/api/research/candles", json=body)
    assert r.status_code == 403 and "only its operator" in r.json()["detail"]
    w.log.close()


def recorded_on(folder, *days):
    s = FileTickStore(folder, keep_days=30, clock=lambda: T0 + timedelta(days=9))
    for d in days:
        at = T0 + timedelta(days=d)
        s.append([(full(at=at), at)])
    return s


async def test_each_subscriptions_recordings_are_its_owners_alone(tmp_path, nifty_fut):
    w = ServerWorld(tmp_path, nifty_fut)  # ayush (admin) owns acc-ayush, riya owns acc-riya
    a, b = MarketData(), MarketData()
    w.platform.prices = PriceSources(w.platform.market_data, per_account=True)
    w.platform.prices.register("acc-ayush", a)
    w.platform.prices.register("acc-riya", b)
    w.users.create_workspace(ACME, "Acme Capital")  # two firms: no "operator only" here
    his = recorded_on(tmp_path / "ticks" / "@acc-ayush", 0, 1)
    hers = recorded_on(tmp_path / "ticks" / "@acc-riya", 2)
    own = {id(a): his, id(b): hers}
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN),
                       ticks_for=lambda source: own.get(id(source)),
                       backtests=Backtests(tmp_path / "runs", w.platform.instruments, MODEL,
                                           ticks=lambda _d: None))  # fmt: skip
    ayush, riya, risk = [await w.logged_in(u) for u in ("ayush", "riya", "risk")]

    def days(listing):
        return [d["day"] for d in listing.json()["days"]]

    assert days(await ayush.get("/api/research/recordings")) == ["2026-10-17", "2026-10-16"]
    assert days(await riya.get("/api/research/recordings")) == ["2026-10-18"]
    assert (await risk.get("/api/research/recordings")).status_code == 404  # no subscription
    assert (await riya.get("/api/research/recordings/2026-10-16")).status_code == 404  # his
    form = (await riya.get("/api/research/backtests/form")).json()
    assert form["days"] == ["2026-10-18"]  # a backtest offers only her own days
    # an admin deletes a day of their own subscription; another's day stays where it is
    assert (await ayush.delete("/api/research/recordings/2026-10-18")).status_code == 404
    assert (await ayush.delete("/api/research/recordings/2026-10-16")).status_code == 200
    assert his.days() == [date(2026, 10, 17)] and hers.days() == [date(2026, 10, 18)]
    assert (await riya.delete("/api/research/recordings/2026-10-18")).status_code == 403
    w.log.close()


def test_each_subscription_is_checked_for_silence_on_its_own(tmp_path):
    now = [datetime(2026, 10, 16, 5, 0, tzinfo=UTC)]
    alerts = Alerts()
    a, b, c = MarketData(), MarketData(), MarketData()
    live = {"acc-a": True, "acc-b": True, "acc-c": False}  # acc-c isn't logged in today
    checks = Checks(alerts, MarketData(), [], tmp_path, clock=lambda: now[0],
                    feeds=[PriceFeed(md, acc, lambda acc=acc: live[acc])
                           for md, acc in ((a, "acc-a"), (b, "acc-b"), (c, "acc-c"))])  # fmt: skip
    now[0] += timedelta(seconds=PRICES_SILENT_S)
    a.on_tick(Tick(FUT, 2500000))  # only A's ticker is flowing
    checks.prices()
    silent = {x.key: x for x in alerts.active()}
    assert set(silent) == {"prices:acc-b"}  # not A (flowing), not C (no login expected)
    assert silent["prices:acc-b"].account_id == "acc-b"  # its viewers' band, nobody else's
    assert silent["prices:acc-b"].text.startswith("acc-b: No prices for over a minute")
    b.on_tick(Tick(FUT, 2500000))
    checks.prices()
    assert alerts.active() == []
    # without feeds: the server's own feed, as before (the simulator)
    shared = MarketData()
    single = Checks(alerts, shared, [], tmp_path, clock=lambda: now[0])
    now[0] += timedelta(seconds=PRICES_SILENT_S)
    single.prices()
    assert [(x.key, x.account_id) for x in alerts.active()] == [("prices", None)]


def test_dropped_ticks_on_any_subscription_raise_the_overload_band():
    alerts = Alerts()
    a, b = MarketData(), MarketData()
    overload = PriceOverload(alerts, a, b)
    overload.check()
    assert alerts.active() == []
    b.dropped += 3  # only the second subscription's readers fell behind
    overload.check()
    assert "3 ticks were dropped" in alerts.active()[0].text
    overload.check()
    assert alerts.active() == []


def filled(log, account_id, price_paise, when):
    request = OrderRequest(f"C{account_id}", account_id, "u", FUT, Side.BUY, 75, OrderType.LIMIT,
                           Product.NRML, price_paise=price_paise)  # fmt: skip
    view = OrderView(request, "B1", OrderStatus.FILLED, 75, price_paise, "", Pending.NONE, when,
                     when)  # fmt: skip
    log.record(OrderEvent(EventKind.FILL, when, account_id, request.client_order_id, view,
                          fill=Fill(request.client_order_id, 75, price_paise, True)))  # fmt: skip


async def test_a_report_values_open_positions_at_the_accounts_own_prices(tmp_path, nifty_fut):
    w = ServerWorld(tmp_path, nifty_fut)
    a, b = MarketData(), MarketData()
    a.on_tick(Tick(FUT, 2501000))
    b.on_tick(Tick(FUT, 2600000))  # another subscription, a very different price
    w.platform.prices = PriceSources(MarketData(), per_account=True)
    w.platform.prices.register("acc-ayush", a)
    w.platform.prices.register("acc-riya", b)
    now = datetime.now(UTC)
    for account_id in ("acc-ayush", "acc-riya"):
        filled(w.log, account_id, 2500000, now - timedelta(minutes=1))
    await w.log.flush()
    reports = Reports(w.platform, None, clock=lambda: now)

    def mark(account_id):
        (row,) = reports.day(account_id, reports.today())["positions"]
        return row["mark_paise"], row["unrealised_paise"]

    assert mark("acc-ayush") == (2501000, 75 * 1000)  # his own subscription's price
    assert mark("acc-riya") == (2600000, 75 * 100000)  # hers
    w.platform.prices = PriceSources(MarketData(), per_account=True)  # no source of its own
    assert mark("acc-ayush")[0] is None  # no marks, rather than another's
    w.log.close()


async def test_a_kite_server_records_each_subscription_in_its_own_folder(tmp_path):
    from test_serve import KEY, env

    from mocktrade.domain.accounts import BrokerAccount, Role
    from mocktrade.server.security import SecretBox
    from mocktrade.server.users import UserStore
    from mocktrade.tools.serve import ServeSettings, build

    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(KEY))
    users.create_workspace(ACME, "Acme Capital")
    users.create_user("ayush", "Ayush", "a-long-password-1", {Role.TRADER})
    users.create_user("ravi", "Ravi", "a-long-password-1", {Role.TRADER}, workspace=ACME)
    users.add_account(BrokerAccount("acc-a", "kite", "AB1234", "ayush"))
    users.add_account(BrokerAccount("acc-r", "kite", "RV0001", "ravi"))
    built = build(ServeSettings.from_env(env(MOCKTRADE_DATA=str(data), MOCKTRADE_PRICES="kite",
                                             KITE_API_KEY="k", KITE_API_SECRET="s")))  # fmt: skip
    try:
        assert set(built.recorders) == {"acc-a", "acc-r"}  # nobody's shared recording
        built.kite.prices_of("acc-a").on_tick(Tick(FUT, 2500000))
        for recorder in built.recorders.values():
            await recorder.flush()
        mine = built.recorders["acc-a"].store
        assert mine.root == data / "ticks" / "@acc-a" and len(mine.days()) == 1
        assert built.recorders["acc-r"].store.days() == []  # acme recorded none of A's ticks
        assert not [p for p in (data / "ticks").iterdir() if not p.name.startswith("@")]
        # a backtest replays its owner's own subscription's days, and their firm's candles
        CandleStores(data / "candles").of(ACME).put(FUT, DAY, CANDLES)
        today = mine.days()[0]
        ticks, candles = built.backtests._sources("ayush")
        assert ticks(today) is not None and candles(FUT, DAY) is None
        ticks, candles = built.backtests._sources("ravi")
        assert ticks(today) is None and candles(FUT, DAY) == CANDLES
        ticks, candles = built.backtests._sources("gone")
        assert ticks(today) is None and candles is None
    finally:
        await built.close()
