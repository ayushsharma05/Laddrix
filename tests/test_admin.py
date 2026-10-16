"""K13.1 (#281): users, roles, accounts and risk limits without code.

The rules, worked through:
- the limits spec covers every RiskLimits field, its defaults are RiskLimits(), and a value
  out of range or of the wrong kind is refused with the reason;
- a limit edit applies from the next order everywhere a limit was copied at the start (the
  checks, the daily-loss stop, the closing pieces), and is saved for a restart;
- a turned-off account takes only closing trades (cancels always work);
- there is always an active admin; nobody turns themselves off;
- a reset password must be changed before anything else;
- an admin's new account runs at once, its loops ended with the platform's.
"""

import asyncio
import json
import sqlite3
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from test_server import FUT, ORDER, PASSWORD, World

from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.spec import check_params
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.accounts import BrokerAccount, Role, User, can_manage, can_trade
from mocktrade.domain.instruments import Exchange, InstrumentKind
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.orders.model import ClientIds
from mocktrade.risk.limits import RiskLimits
from mocktrade.risk.limits_spec import (
    BAND,
    LIMITS_SPEC,
    changes,
    limits_from_dict,
    limits_spec_dict,
    limits_to_dict,
    stored_limits,
    warnings,
)
from mocktrade.server import security
from mocktrade.server.admin import CallbackStarter
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import LastAdmin, UserStore

# --- the limits spec ------------------------------------------------------------------------


def test_the_spec_covers_every_limit_and_its_defaults_are_the_defaults():
    names = {p.name.split(".")[0] for p in LIMITS_SPEC.params}
    assert names == {f.name for f in fields(RiskLimits)}
    assert limits_from_dict({}) == (RiskLimits(), [])
    d = limits_to_dict(RiskLimits())
    assert {p.name: p.default for p in LIMITS_SPEC.params} == d
    assert d["max_order_value_paise"] == 50_00_000_00 and d[f"{BAND}EQ"] == 5.0
    spec = limits_spec_dict()
    kinds = {p["name"]: p["kind"] for p in spec["params"]}
    assert kinds["allowed_exchanges"] == "choices" and kinds["max_daily_loss_paise"] == "money"
    assert kinds["blocked_instruments"] == "instruments" and kinds[f"{BAND}CE"] == "percent"


def test_round_trip_and_every_kind_of_refusal():
    raw = {**limits_to_dict(RiskLimits()), "max_order_lots": 3, "allowed_exchanges": ["NSE", "NFO",
           "NSE"], "blocked_instruments": ["NSE:YESBANK"], f"{BAND}CE": 25,
           "max_daily_loss_paise": 5_000_00, "duplicate_window_s": 0}  # fmt: skip
    limits, problems = limits_from_dict(raw)
    assert problems == []
    assert limits.max_order_lots == 3 and limits.max_daily_loss_paise == 5_000_00
    assert limits.allowed_exchanges == {Exchange.NSE, Exchange.NFO}  # each once
    assert limits.price_band_pct[InstrumentKind.CALL] == Decimal("25")
    assert limits.duplicate_window_s == 0.0 and limits.blocked_instruments == {"NSE:YESBANK"}
    assert limits_from_dict(limits_to_dict(limits)) == (limits, [])
    for bad, words in [
        ({"max_order_lots": 0}, "Most lots per order must be at least 1"),
        ({"max_order_lots": 10_001}, "Most lots per order must be at most 10000"),
        ({"max_order_lots": 2.5}, "Most lots per order: must be a whole number"),
        ({"allowed_exchanges": []}, "Exchanges must be at least 1 chosen"),
        ({"allowed_exchanges": ["LSE"]}, "Exchanges: LSE: not one of"),
        ({"allowed_kinds": "EQ"}, "Instrument kinds: must be a list"),
        ({"allow_option_selling": "yes"}, "Allow option selling: must be yes or no"),
        ({f"{BAND}FUT": 0}, "Price band: FUT must be at least 0.5"),
        ({"max_daily_loss_paise": 999_99}, "Daily loss limit must be at least 100000"),
        ({"duplicate_window_s": 11}, "Double-click window must be at most 10"),
        (
            {"blocked_instruments": [f"NSE:S{n}" for n in range(201)]},
            "Blocked contracts must be at most 200 chosen",
        ),
        ({"surprise": 1}, "Risk limits has no setting called 'surprise'"),
    ]:
        limits, problems = limits_from_dict(bad)
        assert limits is None and any(words in p for p in problems), (bad, problems)


def test_lists_come_back_each_once_in_order():
    clean, problems = check_params(LIMITS_SPEC, {"blocked_instruments": ["NSE:B", "NSE:A",
                                                                         "NSE:A"]})  # fmt: skip
    assert problems == [] and clean["blocked_instruments"] == ["NSE:A", "NSE:B"]
    many = [f"NSE:S{n}" for n in range(200)] + ["NSE:S0"]  # 200 different, one twice
    assert check_params(LIMITS_SPEC, {"blocked_instruments": many})[1] == []


def test_saved_limits_from_another_version_never_stop_an_account():
    assert stored_limits(None) == RiskLimits()
    got = stored_limits({"max_order_lots": 4, "gone_limit": 1, "max_open_orders": 0})
    assert got.max_order_lots == 4  # kept
    assert got.max_open_orders == RiskLimits().max_open_orders  # out of range: the default


def test_what_an_edit_changes_looser_or_tighter():
    old = RiskLimits()
    new = replace(old, max_order_lots=5, duplicate_window_s=0.0, allow_option_selling=True,
                  allowed_exchanges=frozenset({Exchange.NSE}), require_market_price=False,
                  blocked_instruments=frozenset({"NSE:YESBANK"}),
                  price_band_pct={**old.price_band_pct,
                                  InstrumentKind.PUT: Decimal("50")})  # fmt: skip
    got = {c["name"]: (c["looser"], c["text"]) for c in changes(old, new)}
    assert got == {
        "allowed_exchanges": (False, "Exchanges: BFO, BSE, NFO, NSE → NSE"),
        "blocked_instruments": (False, "Blocked contracts: none → NSE:YESBANK"),
        "allow_option_selling": (True, "Allow option selling: no → yes"),
        "max_order_lots": (False, "Most lots per order: 10 → 5"),
        f"{BAND}PE": (True, "Price band: PE: 40% → 50%"),
        "require_market_price": (True, "Need a live price: yes → no"),
        "duplicate_window_s": (True, "Double-click window: 1 s → 0 s"),
    }
    back = {c["name"]: c["looser"] for c in changes(new, old)}
    assert back == {k: not v for k, (v, _) in got.items()}  # the way back is the other way
    assert changes(old, old) == []
    money = changes(old, replace(old, max_daily_loss_paise=30_000_00))[0]
    assert money["looser"] and money["text"] == "Daily loss limit: ₹20,000.00 → ₹30,000.00"


def test_what_a_tighter_limit_means_for_what_is_open():
    tight = replace(RiskLimits(), max_position_lots=2, max_open_orders=1,
                    max_daily_loss_paise=5_000_00,
                    blocked_instruments=frozenset({"NSE:YESBANK"}))  # fmt: skip
    held = {FUT: (-225, 75), "NSE:YESBANK": (10, 1), "NSE:A": (0, 1)}
    flat = replace(tight, blocked_instruments=frozenset({"NSE:YESBANK", "NSE:A"}))
    assert warnings(flat, positions={"NSE:A": (0, 1)}, working_orders=0, day_pnl_paise=0) == []
    said = warnings(tight, positions=held,
                    working_orders=3, day_pnl_paise=-6_000_00)  # fmt: skip
    assert said == [
        f"{FUT}: 3 lots held, above the new 2-lot limit: only orders that reduce it will pass",
        "NSE:YESBANK: 10 lots held, above the new 2-lot limit: only orders that reduce it will "
        "pass",
        "NSE:YESBANK is blocked but held: closing it is still allowed",
        "3 orders are working, above the new limit of 1: no new order until some finish",
        "today's loss of ₹6,000.00 is already beyond the new daily loss limit: trading stops at "
        "the next price change (the daily-loss stop cancels working orders)",
    ]
    exactly = warnings(tight, positions={FUT: (150, 75)}, working_orders=1, day_pnl_paise=-1)
    assert exactly == []  # at the limit is within it
    at_loss = warnings(tight, positions={}, working_orders=0, day_pnl_paise=-5_000_00)
    assert len(at_loss) == 1 and "already beyond" in at_loss[0]  # a loss AT the limit stops


# --- the account at work ----------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    started = []

    def start(account, limits):
        if account.broker != "paper":
            return "only paper here"
        w.brokers[account.id] = FakeBroker()
        w.platform.add_account(account, w.brokers[account.id], ids=ClientIds("NW"),
                               clock=w.clock, algo_types=BUILTIN,
                               limits=stored_limits(limits))  # fmt: skip
        started.append(account.id)
        return None

    w.starter = CallbackStarter(("paper", "kite"), start)
    w.started = started
    w.app = create_app(w.platform, w.users, ServerSettings(origin="https://trade.example.in"),
                       monotonic=w.clock.mono, starter=w.starter)  # fmt: skip
    yield w
    w.log.close()


def request(side, qty, account="acc-ayush", cid="A1"):
    return OrderRequest(cid, account, "ayush", FUT, side, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=2501000)  # fmt: skip


async def test_a_turned_off_account_takes_only_closing_trades(world):
    running = world.platform.account("acc-ayush")
    running.apply_limits(replace(RiskLimits(), max_order_value_paise=10**12))  # sizes only
    running.positions.apply_fill(FUT, Product.NRML, Side.BUY, 150, 2501000)  # long 2 lots
    running.set_active(False)
    refused = await running.risk.admit_new(request(Side.BUY, 75))
    assert refused == ("the account is turned off by an admin: only closing trades are allowed")
    assert await running.risk.admit_new(request(Side.SELL, 150)) is None  # closes it all
    assert "turned off" in await running.risk.admit_new(request(Side.SELL, 225))  # flips short
    mis = replace(request(Side.SELL, 75), product=Product.MIS)
    assert "turned off" in await running.risk.admit_new(mis)  # opens a MIS short: not closing
    working = await running.orders.submit(request(Side.SELL, 75, cid="S1"))  # reduces
    view = running.orders.get("S1") or working
    grown = await running.risk.admit_modify(view, replace(view.request, qty=225))
    assert grown and "turned off" in grown  # growing it past the position: refused
    assert await running.risk.admit_modify(view, replace(view.request, qty=150)) is None
    running.set_active(True)
    assert await running.risk.admit_new(request(Side.BUY, 75)) is None
    stored_off = world.platform.add_account(
        BrokerAccount("acc-off", "paper", "P3", "ayush", active=False), FakeBroker(),
        ids=ClientIds("OF"), clock=world.clock,
    )  # fmt: skip
    assert "turned off" in await stored_off.risk.admit_new(request(Side.BUY, 75, "acc-off"))


async def test_new_limits_reach_everything_that_copied_one(world):
    running = world.platform.add_account(
        BrokerAccount("acc-auto", "paper", "P1", "ayush"), FakeBroker(), ids=ClientIds("AU"),
        clock=world.clock, auto_kill_on_loss=True,
    )  # fmt: skip
    new = replace(RiskLimits(), max_order_lots=2, max_daily_loss_paise=7_000_00)
    running.apply_limits(new)
    assert running.risk.limits is new
    assert running.loss_watch.limit_paise == 7_000_00
    assert running.mtm.max_lots == 2 and running.squareoff.max_lots == 2
    assert "more than the 2-lot" in await running.risk.admit_new(
        request(Side.BUY, 225, "acc-auto"))  # fmt: skip


async def test_an_account_added_while_running_runs_and_stops_with_the_platform(world):
    task = asyncio.ensure_future(world.platform.run(reconcile=False))
    await asyncio.sleep(0)
    broker = FakeBroker()
    late = world.platform.add_account(BrokerAccount("acc-late", "paper", "P2", "ayush"), broker,
                                      ids=ClientIds("LT"), clock=world.clock)  # fmt: skip
    loops = set(world.platform._late)
    assert loops  # its order loop and algos run now, not after a restart
    order = await late.orders.submit(request(Side.BUY, 75, "acc-late", "L1"))
    for _ in range(20):
        if broker.placed:
            break
        await asyncio.sleep(0)
    assert broker.placed and order.request.client_order_id == "L1"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(t.done() for t in loops) and world.platform._late is None


# --- the store ------------------------------------------------------------------------------


def make_store(tmp_path):
    store = UserStore(tmp_path / "u.db", SecretBox(SecretBox.new_key()))
    for uid, roles in (("ana", {Role.ADMIN, Role.TRADER}), ("bo", {Role.TRADER})):
        store.create_user(uid, uid.title(), PASSWORD, roles)
    return store


def test_there_is_always_an_active_admin(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(LastAdmin, match="ana is the last active admin"):
        store.update_user("ana", roles={Role.TRADER})
    with pytest.raises(LastAdmin):
        store.update_user("ana", active=False)
    store.update_user("bo", roles={Role.ADMIN})
    before, after = store.update_user("ana", roles={Role.TRADER}, name=" Ana K ")
    assert before.roles == {Role.ADMIN, Role.TRADER}
    assert after == User("ana", "Ana K", frozenset({Role.TRADER}))
    with pytest.raises(LastAdmin):
        store.update_user("bo", active=False)  # now bo is the last one
    with pytest.raises(ValueError, match="name: 1-80"):
        store.update_user("bo", name="  ")
    with pytest.raises(KeyError):
        store.update_user("nobody", name="X")


def test_turning_off_unlocking_and_resets(tmp_path):
    store = make_store(tmp_path)
    token = store.start_session(store.get_user("bo"))
    store.update_user("bo", active=False)
    assert store.session_user(token) is None  # logged out everywhere
    store.update_user("bo", active=True)
    for _ in range(5):
        with pytest.raises(Exception):  # noqa: B017 (LoginFailed)
            store.verify_login("bo", "wrong password!!", "000000")
    assert [e["locked_until"] is not None for e in store.users() if e["user"].id == "bo"] == [True]
    assert store.unlock("bo") is True and store.unlock("bo") is False
    assert [e["failures"] for e in store.users() if e["user"].id == "bo"] == [0]
    token = store.start_session(store.get_user("bo"))
    temporary = store.reset_password("bo")
    assert len(temporary) == 16 and store.session_user(token) is None
    assert store.get_user("bo").must_change_password
    token = store.start_session(store.get_user("bo"))
    other = store.start_session(store.get_user("bo"))
    with pytest.raises(ValueError, match="current password is wrong"):
        store.change_own_password("bo", "not it", "a brand new password", token)
    with pytest.raises(ValueError, match="at least 12"):
        store.change_own_password("bo", temporary, "short", token)
    with pytest.raises(ValueError, match="must differ"):
        store.change_own_password("bo", temporary, temporary, token)
    store.change_own_password("bo", temporary, "a brand new password", token)
    assert store.session_user(token) is not None and store.session_user(other) is None
    assert not store.get_user("bo").must_change_password
    uri = store.reset_authenticator("bo")
    assert uri.startswith("otpauth://") and store.session_user(token) is None
    for missing in (store.reset_password, store.reset_authenticator):
        with pytest.raises(KeyError):
            missing("nobody")


def test_accounts_and_saved_limits(tmp_path):
    store = make_store(tmp_path)
    store.add_account(BrokerAccount("acc-ana", "paper", "P1", "ana"))
    with pytest.raises(ValueError, match="no such user: zed"):
        store.update_account("acc-ana", traders={"bo", "zed"})
    before, after = store.update_account("acc-ana", traders={"bo", "ana"}, active=False)
    assert after.traders == {"bo"} and not after.active and before.active  # owner not listed
    assert store.get_account("acc-ana") == after
    assert store.account_limits("acc-ana") is None
    store.set_account_limits("acc-ana", {"max_order_lots": 3}, "ana")
    store.set_account_limits("acc-ana", {"max_order_lots": 4}, "ana")
    assert store.account_limits("acc-ana") == {"max_order_lots": 4}
    with pytest.raises(KeyError):
        store.update_account("acc-nope", active=True)


def test_an_older_users_file_gets_the_new_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL,
        password_hash TEXT NOT NULL, totp_secret TEXT NOT NULL, roles TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT, last_totp_step INTEGER NOT NULL DEFAULT 0);
        INSERT INTO users (id, name, password_hash, totp_secret, roles)
        VALUES ('old', 'Old', 'x', 'y', 'admin');""")  # fmt: skip
    db.commit()
    db.close()
    store = UserStore(path, SecretBox(SecretBox.new_key()))
    assert store.get_user("old") == User("old", "Old", frozenset({Role.ADMIN}))
    UserStore(path, SecretBox(SecretBox.new_key()))  # opening again adds nothing twice


def test_who_may_work_a_turned_off_account():
    owner = User("a", "A", frozenset({Role.TRADER}))
    off = BrokerAccount("acc", "paper", "P", "a", active=False)
    assert not can_trade(owner, off) and can_manage(owner, off)
    assert not can_manage(User("b", "B", frozenset({Role.TRADER})), off)
    assert not can_manage(User("r", "R", frozenset({Role.RISK})), off)
    assert not can_manage(replace(owner, active=False), off)


# --- the API --------------------------------------------------------------------------------


async def test_reading_is_for_admin_and_risk_changing_for_admin(world):
    for uid, read, write in (("ayush", 200, 201), ("risk", 200, 403), ("riya", 403, 403)):
        c = await world.logged_in(uid)
        assert (await c.get("/api/admin/users")).status_code == read
        assert (await c.get("/api/admin/accounts")).status_code == read
        assert (await c.get("/api/admin/limits/spec")).status_code == read
        new = await c.post("/api/admin/users", json={"id": f"n-{uid}", "name": "N",
                                                     "roles": ["trader"]})  # fmt: skip
        assert new.status_code == write
        put = await c.put("/api/admin/accounts/acc-riya/limits", json={"limits": {}})
        assert put.status_code == (200 if write == 201 else 403)
        await c.aclose()


async def test_a_new_user_logs_in_and_must_set_a_password_first(world):
    c = await world.logged_in()
    r = await c.post("/api/admin/users", json={"id": "meera", "name": "Meera",
                                              "roles": ["trader", "risk"]})  # fmt: skip
    made = r.json()
    assert made["user"]["roles"] == ["risk", "trader"] and made["user"]["must_change_password"]
    assert "shown once" in made["note"]
    again = await c.post("/api/admin/users", json={"id": "meera", "name": "M", "roles": ["risk"]})
    assert again.status_code == 409 and "already exists" in again.json()["detail"]
    bad = await c.post("/api/admin/users", json={"id": "-x", "name": "X", "roles": ["boss"]})
    assert bad.status_code == 422
    secret = parse_qs(urlparse(made["totp_uri"]).query)["secret"][0]
    m = world.client()
    login = await m.post("/api/session", json={"username": "meera",
                                               "password": made["temporary_password"],
                                               "code": security.totp_code(secret,
                                                   world.clock.now.timestamp())})  # fmt: skip
    assert login.status_code == 200 and login.json()["user"]["must_change_password"]
    blocked = await m.get("/api/instruments")
    assert blocked.status_code == 403 and "set a new password first" in blocked.json()["detail"]
    assert (await m.get("/api/me")).status_code == 200
    weak = await m.post("/api/me/password", json={"old": made["temporary_password"],
                                                  "new": "short"})  # fmt: skip
    assert weak.status_code == 422
    ok = await m.post("/api/me/password", json={"old": made["temporary_password"],
                                                "new": "meera's own long password"})  # fmt: skip
    assert ok.status_code == 204
    assert (await m.get("/api/instruments")).status_code == 200
    kinds = [e.kind for e in world.log.notes("-", ["ADMIN_USER_CREATED", "PASSWORD_CHANGED"])]
    assert kinds == ["ADMIN_USER_CREATED", "PASSWORD_CHANGED"]
    assert made["temporary_password"] not in json.dumps([e.extra for e in world.log.notes(
        "-", ["ADMIN_USER_CREATED"])])  # fmt: skip
    await c.aclose()
    await m.aclose()


async def test_the_lock_out_guards_over_the_api(world):
    c = await world.logged_in()
    me_off = await c.patch("/api/admin/users/ayush", json={"active": False})
    assert me_off.status_code == 409 and "turn yourself off" in me_off.json()["detail"]
    demote = await c.patch("/api/admin/users/ayush", json={"roles": ["trader"]})
    assert demote.status_code == 409 and "last active admin" in demote.json()["detail"]
    changed = await c.patch("/api/admin/users/riya", json={"roles": ["trader", "admin"]})
    assert changed.json()["roles"] == ["admin", "trader"]
    note = world.log.notes("-", ["ADMIN_USER_CHANGED"])[-1]
    assert note.extra["before"]["roles"] == ["trader"] and note.extra["by"] == "ayush"
    assert (await c.patch("/api/admin/users/nobody", json={"name": "N"})).status_code == 404
    r = world.client()
    await r.post("/api/session", json={"username": "riya", "password": PASSWORD,
                                       "code": world.code("riya")})  # fmt: skip
    assert (await r.get("/api/me")).status_code == 200
    off = await c.patch("/api/admin/users/riya", json={"active": False})
    assert off.json()["active"] is False and off.json()["sessions"] == 0
    assert (await r.get("/api/me")).status_code == 401  # logged out at once
    reset = await c.post("/api/admin/users/riya/reset-password", json={})
    assert len(reset.json()["temporary_password"]) == 16
    assert (await c.post("/api/admin/users/riya/reset-authenticator", json={})).json()["totp_uri"]
    assert (await c.post("/api/admin/users/riya/unlock", json={})).status_code == 200
    assert (await c.post("/api/admin/users/nobody/unlock", json={})).status_code == 404
    await c.aclose()
    await r.aclose()


async def test_an_admins_new_account_runs_at_once(world):
    c = await world.logged_in()
    r = await c.post("/api/admin/accounts", json={"id": "acc-meera", "broker": "paper",
                                                 "broker_client_id": "PAPER9", "owner": "riya",
                                                 "traders": ["ayush", "riya"]})  # fmt: skip
    assert r.status_code == 201
    assert r.json() == {"id": "acc-meera", "broker": "paper", "broker_client_id": "PAPER9",
                             "owner": "riya", "traders": ["ayush"], "active": True,
                             "running": True, "not_running": ""}  # fmt: skip
    placed = await c.post("/api/accounts/acc-meera/orders", json=ORDER)
    assert placed.status_code == 201  # ayush may trade it
    kite_body = {"id": "acc-k", "broker": "kite", "broker_client_id": "K1", "owner": "riya"}
    kite = await c.post("/api/admin/accounts", json=kite_body)
    assert kite.json()["running"] is False and kite.json()["not_running"] == "only paper here"
    listed = (await c.get("/api/admin/accounts")).json()
    assert listed["brokers"] == ["paper", "kite"]
    assert {a["id"]: a["not_running"] for a in listed["accounts"]}["acc-k"] == "only paper here"
    for body, code, words in [
        ({"broker": "mt5"}, 422, "this server can add paper, kite accounts, not mt5"),
        ({"owner": "nobody"}, 422, "no such user: nobody"),
        ({"id": "acc-meera"}, 409, "already exists"),
    ]:
        base = {"id": "acc-x", "broker": "paper", "broker_client_id": "X1", "owner": "riya"}
        bad = await c.post("/api/admin/accounts", json={**base, **body})
        assert (bad.status_code, words in bad.json()["detail"]) == (code, True), bad.json()
    assert [e.kind for e in world.log.notes("acc-meera", ["ADMIN_ACCOUNT_CREATED"])]
    await c.aclose()


async def test_turning_an_account_off_over_the_api(world):
    c = await world.logged_in()
    working = (await c.post("/api/accounts/acc-ayush/orders", json=ORDER)).json()
    off = await c.patch("/api/admin/accounts/acc-ayush", json={"active": False})
    assert off.json()["active"] is False
    me = (await c.get("/api/me")).json()
    mine = next(a for a in me["accounts"] if a["id"] == "acc-ayush")
    assert mine["active"] is False and mine["can_trade"] is True  # closing still allowed
    refused = (await c.post("/api/accounts/acc-ayush/orders", json=ORDER)).json()
    assert refused["status"] == "REJECTED" and "turned off by an admin" in refused["reason"]
    cid = working["request"]["client_order_id"]
    cancel = await c.delete(f"/api/accounts/acc-ayush/orders/{cid}")
    assert cancel.status_code == 200  # cancels always work
    assert (await c.post("/api/accounts/acc-ayush/stop", json={"reason": "x"})).status_code == 200
    on = await c.patch("/api/admin/accounts/acc-ayush", json={"active": True, "traders": ["riya"]})
    assert on.json()["traders"] == ["riya"]
    bad = await c.patch("/api/admin/accounts/acc-ayush", json={"traders": ["zed"]})
    assert bad.status_code == 422
    assert (await c.patch("/api/admin/accounts/acc-nope", json={"active": True})).status_code == 404
    note = world.log.notes("acc-ayush", ["ADMIN_ACCOUNT_CHANGED"])[0]
    assert note.extra["before"]["active"] is True and note.extra["after"]["active"] is False
    await c.aclose()


async def test_limits_preview_apply_audit_and_survive_a_restart(world, nifty_fut):
    c = await world.logged_in()
    world.platform.account("acc-ayush").positions.apply_fill(FUT, Product.NRML, Side.BUY, 375,
                                                             2501000)  # 5 lots  # fmt: skip
    got = (await c.get("/api/admin/accounts/acc-ayush/limits")).json()
    assert got["limits"] == limits_to_dict(RiskLimits()) and got["running"] is True
    wanted = {**got["limits"], "max_order_lots": 1, "max_position_lots": 4}
    preview = (await c.put("/api/admin/accounts/acc-ayush/limits?dry_run=true",
                           json={"limits": wanted})).json()  # fmt: skip
    assert preview["applied"] is False
    assert [x["name"] for x in preview["changes"]] == ["max_order_lots", "max_position_lots"]
    assert preview["warnings"] == [f"{FUT}: 5 lots held, above the new 4-lot limit: only "
                                   "orders that reduce it will pass"]  # fmt: skip
    assert world.platform.account("acc-ayush").risk.limits == RiskLimits()  # not yet
    applied = (await c.put("/api/admin/accounts/acc-ayush/limits", json={"limits": wanted})).json()
    assert applied["applied"] is True
    big = (await c.post("/api/accounts/acc-ayush/orders", json={**ORDER, "qty": 150})).json()
    assert big["status"] == "REJECTED" and "1-lot" in big["reason"]
    note = world.log.notes("acc-ayush", ["ADMIN_LIMITS_CHANGED"])
    assert len(note) == 1 and note[0].extra["changes"][0] == {
        "name": "max_order_lots", "old": 10, "new": 1, "looser": False}  # fmt: skip
    same = (await c.put("/api/admin/accounts/acc-ayush/limits", json={"limits": wanted})).json()
    assert same["applied"] is False and same["changes"] == []  # nothing to do, nothing audited
    assert len(world.log.notes("acc-ayush", ["ADMIN_LIMITS_CHANGED"])) == 1
    bad = await c.put("/api/admin/accounts/acc-ayush/limits",
                      json={"limits": {"max_order_lots": 0, "max_open_orders": "x"}})  # fmt: skip
    assert bad.status_code == 422 and "at least 1" in bad.json()["detail"]
    gone = await c.put("/api/admin/accounts/acc-ayush/limits",
                       json={"limits": {"blocked_instruments": ["NSE:NOPE"]}})  # fmt: skip
    assert gone.status_code == 422 and "no such contract NSE:NOPE" in gone.json()["detail"]
    assert (await c.get("/api/admin/accounts/acc-nope/limits")).status_code == 404
    # a restart: the saved limits are the account's again
    assert stored_limits(world.users.account_limits("acc-ayush")).max_order_lots == 1
    await c.aclose()


async def test_the_stream_refuses_a_user_who_must_change_their_password(world):
    temporary = world.users.reset_password("riya")
    user = world.users.verify_login("riya", temporary, world.code("riya"))
    world.clock.now += timedelta(seconds=30)
    token = world.users.start_session(user)
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(world.app, base_url="https://testserver") as tc:
        tc.cookies.set("__Host-mt_session", token)
        with pytest.raises(WebSocketDisconnect) as closed:
            with tc.websocket_connect("/ws", headers={"origin": "https://trade.example.in"}) as ws:
                ws.receive_json()
    assert closed.value.code == 4401


async def test_the_running_account_compares_with_the_broker_by_product(world):
    running = world.platform.account("acc-ayush")
    running.positions.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    running.positions.apply_fill(FUT, Product.MIS, Side.SELL, 75, 2501000)
    from mocktrade.broker.base import BrokerPosition

    world.brokers["acc-ayush"].broker_positions = [
        BrokerPosition(FUT, 75, 0, product=Product.NRML),
        BrokerPosition(FUT, -75, 0, product=Product.MIS),
    ]
    assert (await running.reconciler.check()).ok  # #258: not a false difference
