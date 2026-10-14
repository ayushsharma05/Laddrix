"""The HTTP API end to end: login, protections, orders, positions, kill switches, broker login.

The real app runs in-process over https (so Secure cookies work as in a browser),
wired by Platform.add_account to a fake broker.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.server import security
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog

ORIGIN = "https://trade.example.in"
FUT = "NFO:NIFTY26OCTFUT"
PASSWORD = "a long enough password"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def mono(self):
        return self.now.timestamp()


class World:
    def __init__(self, tmp_path, nifty_fut):
        self.clock = Clock()
        self.users = UserStore(
            tmp_path / "users.db", SecretBox(SecretBox.new_key()), clock=self.clock
        )
        self.secrets = {}
        self.add_user("ayush", {Role.TRADER, Role.ADMIN})
        self.add_user("riya", {Role.TRADER})
        self.add_user("risk", {Role.RISK})
        self.users.add_account(BrokerAccount("acc-ayush", "kite", "AB1234", "ayush"))
        self.users.add_account(BrokerAccount("acc-riya", "kite", "RY5678", "riya"))
        store = InstrumentStore()
        store.add(nifty_fut)
        md = MarketData()
        md.on_tick(Tick(FUT, 2501000))
        self.log = EventLog(tmp_path / "events.db")
        self.log.start()
        self.platform = Platform(store, md, KillSwitch("firm"), self.log)
        self.brokers = {}
        for account in self.users.accounts():
            self.brokers[account.id] = FakeBroker()
            self.platform.add_account(
                account, self.brokers[account.id], ids=ClientIds("MT"), clock=self.clock
            )
        self.login_calls = []
        self.app = create_app(
            self.platform,
            self.users,
            ServerSettings(origin=ORIGIN),
            broker_login=self,
            monotonic=self.clock.mono,
        )

    def add_user(self, user_id, roles):
        created = self.users.create_user(user_id, user_id.title(), PASSWORD, roles)
        self.secrets[user_id] = parse_qs(urlparse(created.totp_uri).query)["secret"][0]

    def code(self, user_id, offset_s=0):
        return security.totp_code(self.secrets[user_id], self.clock.now.timestamp() + offset_s)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://testserver",
            headers={"origin": ORIGIN},
        )

    async def logged_in(self, user_id="ayush") -> httpx.AsyncClient:
        client = self.client()
        r = await client.post(
            "/api/session",
            json={"username": user_id, "password": PASSWORD, "code": self.code(user_id)},
        )
        assert r.status_code == 200, r.text
        self.clock.now += timedelta(seconds=30)  # next login needs a fresh code
        return client

    # the broker login hook (BrokerLogin protocol)
    def login_url(self, account, state):
        return f"https://kite.example/login?state={state}&account={account.id}"

    async def complete(self, account, request_token):
        if request_token == "bad":
            raise RuntimeError("Kite refused the token")
        self.login_calls.append((account.id, request_token))


@pytest.fixture
async def world(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    yield w
    w.log.close()


ORDER = {
    "instrument_id": FUT,
    "side": "BUY",
    "qty": 75,
    "order_type": "LIMIT",
    "product": "NRML",
    "price_paise": 2501000,
}


# --- the basics ---


async def test_health_needs_no_login_and_every_response_has_security_headers(world):
    async with world.client() as c:
        r = await c.get("/api/health")
    assert r.json() == {"status": "ok"}
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "max-age" in r.headers["strict-transport-security"]


async def test_no_docs_pages_are_exposed(world):
    async with world.client() as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/me"),
        ("GET", "/api/accounts/acc-ayush/orders"),
        ("GET", "/api/accounts/acc-ayush/positions"),
    ],
)
async def test_everything_else_needs_a_session(world, method, path):
    async with world.client() as c:
        assert (await c.request(method, path)).status_code == 401


# --- login ---


async def test_login_sets_a_locked_down_cookie(world):
    async with world.client() as c:
        r = await c.post(
            "/api/session",
            json={"username": "ayush", "password": PASSWORD, "code": world.code("ayush")},
        )
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("__Host-mt_session=")
        for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            assert part.lower() in cookie.lower()
        me = (await c.get("/api/me")).json()
    assert me["user"] == {"id": "ayush", "name": "Ayush", "roles": ["admin", "trader"]}
    assert [a["id"] for a in me["accounts"]] == ["acc-ayush", "acc-riya"]  # admin sees all


@pytest.mark.parametrize(
    "username, password, code",
    [
        ("ayush", "the wrong password!!", "real"),
        ("ayush", PASSWORD, "000000"),
        ("nobody", PASSWORD, "123456"),
    ],
)
async def test_any_wrong_part_gives_the_same_answer(world, username, password, code):
    if code == "real":
        code = world.code("ayush")
    async with world.client() as c:
        r = await c.post(
            "/api/session", json={"username": username, "password": password, "code": code}
        )
    assert (r.status_code, r.json()["detail"]) == (401, "wrong username, password or code")


async def test_a_code_works_only_once(world):
    body = {"username": "ayush", "password": PASSWORD, "code": world.code("ayush")}
    async with world.client() as c:
        assert (await c.post("/api/session", json=body)).status_code == 200
        assert (await c.post("/api/session", json=body)).status_code == 401


async def test_five_failures_lock_the_user_for_fifteen_minutes(world):
    async with world.client() as c:
        for _ in range(5):
            bad = {"username": "riya", "password": "the wrong password!!", "code": "123456"}
            await c.post("/api/session", json=bad)
        good = {"username": "riya", "password": PASSWORD, "code": world.code("riya")}
        r = await c.post("/api/session", json=good)
        assert "too many failed attempts" in r.json()["detail"]
        world.clock.now += timedelta(minutes=16)
        good["code"] = world.code("riya")
        assert (await c.post("/api/session", json=good)).status_code == 200


async def test_too_many_attempts_from_one_address(world):
    async with world.client() as c:
        for _ in range(20):
            bad = {"username": "x", "password": "the wrong password!!", "code": "123456"}
            assert (await c.post("/api/session", json=bad)).status_code == 401
        assert (await c.post("/api/session", json=bad)).status_code == 429


async def test_logins_are_audited(world):
    async with world.client() as c:
        bad = {"username": "ayush", "password": "the wrong password!!", "code": "123456"}
        await c.post("/api/session", json=bad)
    await world.logged_in()
    await world.log.flush()
    kinds = [(e.kind, e.detail) for e in world.log.events()]
    assert ("LOGIN_FAILED", "ayush") in kinds and ("LOGIN_OK", "ayush") in kinds


async def test_logout_ends_the_session_on_the_server(world):
    c = await world.logged_in()
    stolen = dict(c.cookies)
    assert (await c.delete("/api/session")).status_code == 204
    async with world.client() as thief:
        thief.cookies.update(stolen)
        assert (await thief.get("/api/me")).status_code == 401
    await c.aclose()


async def test_sessions_end_after_two_idle_hours_and_twelve_hours_in_all(world):
    c = await world.logged_in()
    world.clock.now += timedelta(hours=1, minutes=59)
    assert (await c.get("/api/me")).status_code == 200
    for _ in range(6):  # active every 1h59m...
        world.clock.now += timedelta(hours=1, minutes=59)
        last = (await c.get("/api/me")).status_code
    assert last == 401  # ...but never past 12 hours
    c2 = await world.logged_in()
    world.clock.now += timedelta(hours=2, minutes=1)
    assert (await c2.get("/api/me")).status_code == 401
    await c.aclose()
    await c2.aclose()


async def test_disabling_a_user_or_changing_a_password_ends_their_sessions(world):
    c = await world.logged_in("riya")
    world.users.set_password("riya", "another long password")
    assert (await c.get("/api/me")).status_code == 401
    c2 = await world.logged_in("ayush")
    world.users.set_active("ayush", False)
    assert (await c2.get("/api/me")).status_code == 401
    await c.aclose()
    await c2.aclose()


# --- protections on changing requests ---


async def test_changing_requests_must_come_from_our_own_page(world):
    c = await world.logged_in()
    path = "/api/accounts/acc-ayush/orders"
    r = await c.post(path, json=ORDER, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    del c.headers["origin"]
    assert (await c.post(path, json=ORDER)).status_code == 403
    c.headers["origin"] = ORIGIN
    form = await c.post(path, data={"qty": "75"})  # what a cross-site form would send
    assert form.status_code == 415
    assert world.brokers["acc-ayush"].placed == []
    await c.aclose()


async def test_the_body_can_never_choose_the_user_or_account(world):
    c = await world.logged_in("riya")
    smuggled = {**ORDER, "user_id": "ayush"}
    assert (await c.post("/api/accounts/acc-riya/orders", json=smuggled)).status_code == 422
    r = await c.post("/api/accounts/acc-riya/orders", json=ORDER)
    order = r.json()
    assert order["request"]["user_id"] == "riya" and order["request"]["account_id"] == "acc-riya"
    await c.aclose()


async def test_other_peoples_accounts_dont_exist_for_you(world):
    c = await world.logged_in("riya")
    assert (await c.get("/api/accounts/acc-ayush/orders")).status_code == 404
    assert (await c.post("/api/accounts/acc-ayush/orders", json=ORDER)).status_code == 404
    me = (await c.get("/api/me")).json()
    assert [a["id"] for a in me["accounts"]] == ["acc-riya"]
    await c.aclose()


async def test_risk_can_watch_but_not_trade(world):
    c = await world.logged_in("risk")
    assert (await c.get("/api/accounts/acc-ayush/orders")).status_code == 200
    r = await c.post("/api/accounts/acc-ayush/orders", json=ORDER)
    assert (r.status_code, r.json()["detail"]) == (
        403,
        "you can view this account but not trade it",
    )
    await c.aclose()


# --- orders ---


async def test_place_goes_through_the_order_manager(world):
    c = await world.logged_in()
    r = await c.post("/api/accounts/acc-ayush/orders", json={**ORDER, "client_order_id": "W1"})
    assert r.status_code == 201
    order = r.json()
    assert (order["request"]["client_order_id"], order["status"]) == ("W1", "PENDING")
    assert world.brokers["acc-ayush"].placed[0].client_order_id == "W1"
    again = await c.post("/api/accounts/acc-ayush/orders", json={**ORDER, "client_order_id": "W1"})
    assert again.json() == order and len(world.brokers["acc-ayush"].placed) == 1  # retry-safe
    listed = (await c.get("/api/accounts/acc-ayush/orders")).json()
    assert [o["request"]["client_order_id"] for o in listed] == ["W1"]
    await c.aclose()


async def test_a_risk_refusal_comes_back_as_a_rejected_order(world):
    c = await world.logged_in()
    r = await c.post("/api/accounts/acc-ayush/orders", json={**ORDER, "price_paise": 2701000})
    order = r.json()
    assert order["status"] == "REJECTED" and "check for a typo" in order["reason"]
    await c.aclose()


async def test_modify_and_cancel_with_readable_refusals(world):
    c = await world.logged_in()
    placed = (await c.post("/api/accounts/acc-ayush/orders", json=ORDER)).json()
    cid = placed["request"]["client_order_id"]
    path = f"/api/accounts/acc-ayush/orders/{cid}"
    r = await c.patch(path, json={"price_paise": 2500990})
    assert r.json()["request"]["price_paise"] == 2500990
    bad = await c.patch(path, json={"price_paise": 2500995})
    assert bad.status_code == 409 and "tick" in bad.json()["detail"]
    assert (await c.delete(path)).json()["pending"] == "CANCEL"
    missing = await c.delete("/api/accounts/acc-ayush/orders/NOPE")
    assert (missing.status_code, missing.json()["detail"]) == (409, "unknown order NOPE")
    await c.aclose()


async def test_an_orders_audit_history(world):
    c = await world.logged_in()
    placed = (await c.post("/api/accounts/acc-ayush/orders", json=ORDER)).json()
    cid = placed["request"]["client_order_id"]
    world.platform.accounts["acc-ayush"].orders.apply(
        OrderUpdate(cid, "F1", OrderStatus.FILLED, 75, 2501000)
    )
    await world.log.flush()
    history = (await c.get(f"/api/accounts/acc-ayush/orders/{cid}/history")).json()
    assert [h["kind"] for h in history] == ["SUBMITTED", "SENT", "UPDATED", "FILL"]
    assert history[-1]["fill"]["qty"] == 75
    assert (await c.get("/api/accounts/acc-ayush/orders/NOPE/history")).status_code == 404
    await c.aclose()


async def test_an_accounts_audit_trail_in_pages(world):
    c = await world.logged_in()
    for _ in range(3):
        await c.post("/api/accounts/acc-ayush/orders", json=ORDER)
    await c.post("/api/accounts/acc-ayush/stop", json={"reason": "break"})
    await world.log.flush()
    page = (await c.get("/api/accounts/acc-ayush/events", params={"limit": 4})).json()
    kinds = [e["kind"] for e in page]
    # STOP engages the switch first, then cancels the working orders: the cancels are newer
    assert kinds.index("KILL_SWITCH") > kinds.index("CANCEL_SENT")
    stop = page[kinds.index("KILL_SWITCH")]
    assert stop["detail"] == "break" and stop["extra"]["by"] == "ayush"
    seqs = [e["seq"] for e in page]
    assert seqs == sorted(seqs, reverse=True)
    older = (await c.get("/api/accounts/acc-ayush/events", params={"before_seq": seqs[-1]})).json()
    assert all(e["seq"] < seqs[-1] for e in older)
    assert {e["account_id"] for e in page + older} <= {"acc-ayush", "account acc-ayush", "firm"}
    assert any(e["order"] and e["order"]["request"]["user_id"] == "ayush" for e in older)
    await c.aclose()
    # a plain trader can't read someone else's audit trail: the account doesn't exist for them
    riya = await world.logged_in("riya")
    assert (await riya.get("/api/accounts/acc-ayush/events")).status_code == 404
    await riya.aclose()


# --- positions ---


async def test_positions_with_pnl_and_reconcile_state(world):
    c = await world.logged_in()
    book = world.platform.accounts["acc-ayush"].positions
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 10005)
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 10010)
    world.platform.mark(Tick(FUT, 10100))
    body = (await c.get("/api/accounts/acc-ayush/positions")).json()
    (p,) = body["positions"]
    assert (p["qty"], p["avg_price_paise"], p["unrealised_paise"]) == (150, "10007.5", 13875)
    assert body["day_pnl_paise"] == 13875
    assert body["reconcile"] == {"checked_at": None, "ok": None, "error": "", "alerts": []}
    await c.aclose()


# --- kill switches ---


async def test_stop_and_resume_an_account(world):
    c = await world.logged_in("riya")
    await c.post("/api/accounts/acc-riya/orders", json=ORDER)
    r = await c.post("/api/accounts/acc-riya/stop", json={"reason": "feeling unsure"})
    assert r.json() == {"stopped": True, "failed_cancels": []}
    assert world.brokers["acc-riya"].cancelled == ["F1"]
    blocked = (await c.post("/api/accounts/acc-riya/orders", json=ORDER)).json()
    assert "feeling unsure" in blocked["reason"]
    me = (await c.get("/api/me")).json()
    assert me["accounts"][0]["stopped"] and me["accounts"][0]["stop_reason"] == "feeling unsure"
    assert (await c.post("/api/accounts/acc-riya/resume", json={})).json() == {"stopped": False}
    await c.aclose()


async def test_who_may_stop_and_resume(world):
    risk = await world.logged_in("risk")
    riya = await world.logged_in("riya")
    assert (await risk.post("/api/accounts/acc-riya/stop", json={"reason": "check"})).is_success
    assert (await risk.post("/api/firm/stop", json={"reason": "exchange issue"})).is_success
    assert (await riya.post("/api/firm/stop", json={"reason": "x"})).status_code == 403
    assert (await risk.post("/api/firm/resume", json={})).status_code == 403  # admin only
    ayush = await world.logged_in("ayush")
    assert (await ayush.post("/api/firm/resume", json={})).is_success
    assert not world.platform.firm_switch.engaged
    for c in (risk, riya, ayush):
        await c.aclose()


# --- the broker's daily login ---


async def test_broker_login_round_trip_with_a_one_time_state(world):
    c = await world.logged_in()
    url = (await c.get("/api/accounts/acc-ayush/broker-login")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    callback = f"/kite/callback?status=success&request_token=rt1&state={state}"
    r = await c.get(callback)
    assert (r.status_code, r.headers["location"]) == (303, "/?broker_login=ok")
    assert world.login_calls == [("acc-ayush", "rt1")]
    again = await c.get(callback)  # the same state can't be used twice
    assert again.headers["location"] == "/?broker_login=failed"
    await c.aclose()


async def test_broker_login_refuses_strangers_and_bad_answers(world):
    ayush = await world.logged_in()
    url = (await ayush.get("/api/accounts/acc-ayush/broker-login")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    riya = await world.logged_in("riya")
    stolen = await riya.get(f"/kite/callback?status=success&request_token=rt&state={state}")
    assert stolen.headers["location"] == "/?broker_login=failed"  # someone else's state
    url = (await ayush.get("/api/accounts/acc-ayush/broker-login")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    bad = await ayush.get(f"/kite/callback?status=success&request_token=bad&state={state}")
    assert bad.headers["location"] == "/?broker_login=failed"
    assert world.login_calls == []
    other = await ayush.get("/api/accounts/acc-riya/broker-login")
    assert other.status_code == 403  # admins can view it, but only Riya logs in to her broker
    await world.log.flush()
    assert "BROKER_LOGIN_FAILED" in [e.kind for e in world.log.events()]
    await ayush.aclose()
    await riya.aclose()


async def test_broker_login_state_expires(world):
    c = await world.logged_in()
    url = (await c.get("/api/accounts/acc-ayush/broker-login")).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    world.clock.now += timedelta(minutes=11)
    r = await c.get(f"/kite/callback?status=success&request_token=rt&state={state}")
    assert r.headers["location"] == "/?broker_login=failed"
    await c.aclose()


# --- instruments and layouts ---------------------------------------------------------------------


async def test_instrument_search_and_detail(world):
    c = await world.logged_in()
    found = (await c.get("/api/instruments", params={"q": "nifty"})).json()
    assert [i["id"] for i in found] == [FUT]
    detail = (await c.get(f"/api/instruments/{FUT}")).json()
    assert (detail["lot_size"], detail["tick_paise"], detail["ltp_paise"]) == (75, 10, 2501000)
    assert (await c.get("/api/instruments/NSE:NOPE")).status_code == 404
    await c.aclose()


async def test_volume_at_price(world):
    c = await world.logged_in()
    md = world.platform.market_data
    md.on_tick(Tick(FUT, 2501000, volume=1000))
    md.on_tick(Tick(FUT, 2501010, volume=1150))
    md.on_tick(Tick(FUT, 2501000, volume=1225))
    body = (await c.get(f"/api/instruments/{FUT}/vap")).json()
    assert body == {"volume": 1225, "levels": [[2501000, 75], [2501010, 150]]}
    assert (await c.get("/api/instruments/NSE:NOPE/vap")).status_code == 404
    await c.aclose()


async def test_layouts_are_saved_per_user(world):
    ayush = await world.logged_in("ayush")
    riya = await world.logged_in("riya")
    layout = {"widgets": [{"type": "ladder", "instrument": FUT, "link": "pink"}]}
    assert (await ayush.put("/api/layouts/Main", json={"layout": layout})).status_code == 200
    assert (await ayush.get("/api/layouts/Main")).json()["layout"] == layout
    assert [x["name"] for x in (await ayush.get("/api/layouts")).json()] == ["Main"]
    assert (await riya.get("/api/layouts/Main")).status_code == 404  # her own layouts only
    assert (await ayush.put("/api/layouts/bad%2Fname", json={"layout": {}})).status_code in (
        404,
        422,
    )
    huge = {"x": "y" * 300_000}
    assert (await ayush.put("/api/layouts/Big", json={"layout": huge})).status_code == 422
    assert (await ayush.delete("/api/layouts/Main")).status_code == 204
    assert (await ayush.delete("/api/layouts/Main")).status_code == 404
    for c in (ayush, riya):
        await c.aclose()


async def test_layout_count_is_capped(world, monkeypatch):
    from mocktrade.server import users as users_module

    monkeypatch.setattr(users_module, "MAX_LAYOUTS_PER_USER", 2)
    c = await world.logged_in()
    for name in ("A", "B"):
        assert (await c.put(f"/api/layouts/{name}", json={"layout": {}})).status_code == 200
    assert (await c.put("/api/layouts/C", json={"layout": {}})).status_code == 422
    assert (await c.put("/api/layouts/A", json={"layout": {"v": 2}})).status_code == 200
    await c.aclose()


async def test_the_built_screens_are_served_without_shadowing_the_api(world, tmp_path):
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>MockTrade</title>")
    (ui / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("not for the web")
    app = create_app(world.platform, world.users, ServerSettings(origin=ORIGIN), static_dir=ui)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as c:
        assert "MockTrade" in (await c.get("/")).text
        assert "MockTrade" in (await c.get("/workspace/main")).text  # app routes → index
        asset = await c.get("/assets/app-abc123.js")
        assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
        assert "not for the web" not in (await c.get("/../secret.txt")).text
        assert "not for the web" not in (await c.get("/%2e%2e/secret.txt")).text
        assert (await c.get("/api/nothing-here")).status_code == 404
        assert (await c.get("/api/me")).status_code == 401  # still the API
