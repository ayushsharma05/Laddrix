"""Two firms on one server never reach each other (#368a, docs/design/tenancy.md).

    main: ayush (admin, trader), riya (trader), risk (risk); acc-ayush, acc-riya
    acme: ravi (admin, trader), tara (trader), rina (risk);  acc-ravi (traders: tara)

main's admin, risk user and trader try every route of the app with acme's ids. The routes
are read from the app itself, so a route added later is tried too. Every answer is refused
or carries nothing of acme's, and afterwards acme is exactly as it was.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.routing import APIRoute
from test_server import FUT, ORDER, ORIGIN, PASSWORD, World

from laddrix.algos.builtin import BUILTIN
from laddrix.broker.fake import FakeBroker
from laddrix.domain.accounts import BrokerAccount, Role, User, can_manage, can_trade, can_view
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.ops.alerts import Alerts
from laddrix.orders.model import ClientIds
from laddrix.server.app import ServerSettings, create_app

ACME = "acme"
THEIRS = (
    "acc-ravi",
    "ravi",
    "tara",
    "rina",
    "Ravi",
    "Tara",
    "Rina",
    "acme calendar",
)  # never in our answers (the last: ravi's spread, #414)


class TwoFirms(World):
    """test_server's World (firm "main"), plus a second firm on the same server."""

    def __init__(self, tmp_path, nifty_fut):
        super().__init__(tmp_path, nifty_fut)
        self.users.create_workspace(ACME, "Acme Capital")
        for user_id, roles in (("ravi", {Role.ADMIN, Role.TRADER}), ("tara", {Role.TRADER}),
                               ("rina", {Role.RISK})):  # fmt: skip
            made = self.users.create_user(user_id, user_id.title(), PASSWORD, roles,
                                          workspace=ACME)  # fmt: skip
            self.secrets[user_id] = parse_qs(urlparse(made.totp_uri).query)["secret"][0]
        self.users.add_account(BrokerAccount("acc-ravi", "kite", "RV0001", "ravi",
                                             frozenset({"tara"})))  # fmt: skip
        account = self.users.get_account("acc-ravi")
        self.brokers[account.id] = FakeBroker()
        self.platform.add_account(account, self.brokers[account.id], ids=ClientIds("AC"),
                                  clock=self.clock, algo_types=BUILTIN)  # fmt: skip
        # a server with ops bands (test_server's World has none), so they can be checked too
        self.alerts = Alerts()
        self.app = create_app(
            self.platform, self.users, ServerSettings(origin=ORIGIN), broker_login=self,
            broker_status=lambda a: None, monotonic=self.clock.mono, protection=self.protection,
            alerts=self.alerts,
        )  # fmt: skip
        # acme has a band and a surveillance alert: neither may reach main
        self.alerts.raise_("mtm:acc-ravi:x", "ERROR", "acc-ravi MTM (ravi)", account_id="acc-ravi")
        self.platform.surveillance._raise("LARGE", "acc-ravi", FUT, "acc-ravi: ravi's large order")


@pytest.fixture
async def firms(tmp_path, nifty_fut):
    w = TwoFirms(tmp_path, nifty_fut)
    yield w
    w.log.close()


def test_the_rule_itself_another_firms_account_is_nobodys_business():
    theirs = BrokerAccount("acc-ravi", "kite", "RV0001", "ravi", frozenset({"tara"}),
                           workspace=ACME)  # fmt: skip
    ours_admin = User("ayush", "Ayush", frozenset({Role.ADMIN, Role.TRADER, Role.RISK}))
    for check in (can_view, can_manage, can_trade):
        assert not check(ours_admin, theirs)
    # even a user with the owner's id and every role, in another workspace, gets nothing
    lookalike = User("ravi", "Ravi", frozenset({Role.ADMIN, Role.TRADER}))
    assert not any(check(lookalike, theirs) for check in (can_view, can_manage, can_trade))
    owner = User("ravi", "Ravi", frozenset({Role.TRADER}), workspace=ACME)
    assert can_trade(owner, theirs)


async def theirs_running(w):
    """Acme trades: a working order, a stopped account's switch untouched, saved limits."""
    ravi = await w.logged_in("ravi")
    placed = await ravi.post("/api/accounts/acc-ravi/orders", json=ORDER)
    assert placed.status_code == 201, placed.text
    w.platform.instruments.add(Instrument(Exchange.NFO, "NIFTY26NOVFUT", InstrumentKind.FUTURE,
                                          tick_paise=10, lot_size=75))  # fmt: skip
    legs = [{"instrument_id": FUT, "ratio": 1, "multiplier": 1, "quote": True},
            {"instrument_id": "NFO:NIFTY26NOVFUT", "ratio": -1, "multiplier": -1}]  # fmt: skip
    spread = await ravi.post("/api/spreads", json={"name": "acme calendar", "legs": legs})
    assert spread.status_code == 201, spread.text
    w.theirs_spread = spread.json()["spread_id"]
    return ravi, placed.json()["request"]["client_order_id"]


def fill(path: str, theirs_order: str, theirs_spread: str = "none") -> str:
    values = {"account_id": "acc-ravi", "user_id": "ravi", "client_order_id": theirs_order,
              "spread_id": theirs_spread, "structure_id": "s1",
              "algo_id": "A1", "gtt_id": "1", "alert_id": "S1", "run_id": "r1",
              "day": "2026-10-14", "name": "Main", "instrument_id": FUT, "underlying": "NIFTY",
              "expiry": "2026-10-27", "session_id": "0" * 16, "action": "stop",
              "path": "x"}  # fmt: skip
    for key, value in values.items():
        path = path.replace("{" + key + "}", value).replace("{" + key + ":path}", value)
    assert "{" not in path, path
    return path


def every_route(routes, prefix=""):
    """(method, path) of every HTTP route, routers included: this FastAPI keeps an included
    router as one wrapped entry (it once hid the 13 admin routes from this sweep)."""
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield method, prefix + route.path
        elif hasattr(route, "original_router"):
            extra = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from every_route(route.original_router.routes, prefix + extra)


BODIES = {  # a body that passes validation, so the route's own check is what answers
    ("PUT", "/api/spreads/{spread_id}"): {"name": "taken", "legs": []},
    ("POST", "/api/surveillance/{alert_id}/ack"): {"note": "looked at it closely"},
}

SKIP = {
    ("POST", "/api/session"): "logging in: no session yet, nothing of anyone's",
    ("DELETE", "/api/session"): "logging out ends the caller's own session only",
    ("GET", "/{path:path}"): "the screens' files: the same for everyone",
    ("GET", "/kite/callback"): "bound to the user who started it (test_server)",
}


async def test_every_route_refuses_or_hides_another_firm(firms):
    w = firms
    ravi, order = await theirs_running(w)
    before = {"orders": (await ravi.get("/api/accounts/acc-ravi/orders")).json(),
              "limits": w.users.account_limits("acc-ravi"),
              "account": w.users.get_account("acc-ravi"),
              "users": [w.users.get_user(u) for u in ("ravi", "tara", "rina")]}  # fmt: skip
    routes = [r for r in every_route(w.app.routes) if r not in SKIP]
    assert len(routes) >= 115  # every route the app has, read from the app
    assert ("PUT", "/api/admin/accounts/{account_id}/limits") in routes  # routers included
    tried = 0
    for who in ("ayush", "risk", "riya"):  # main's admin (also a trader), risk, trader
        client = await w.logged_in(who)
        for method, template in routes:
            path = fill(template, order, w.theirs_spread)
            body = BODIES.get((method, template), {}) if method != "GET" else None
            r = await client.request(method, path, json=body)
            tried += 1
            if r.status_code < 300:  # allowed: then it must hold nothing of acme's
                for marker in THEIRS:
                    assert marker not in r.text, (who, method, path, r.status_code, marker)
                assert order not in r.text, (who, method, path)
    assert tried == 3 * len(routes)

    # and acme is exactly as it was: nothing of theirs changed, stopped or reset
    assert (await ravi.get("/api/accounts/acc-ravi/orders")).json() == before["orders"]
    assert w.users.account_limits("acc-ravi") == before["limits"]
    assert w.users.get_account("acc-ravi") == before["account"]
    assert [w.users.get_user(u) for u in ("ravi", "tara", "rina")] == before["users"]
    assert not w.platform.firm_switch_of(ACME).engaged
    assert not w.platform.accounts["acc-ravi"].switch.engaged
    assert [a.acked_by for a in w.platform.surveillance.alerts()] == [""]  # nobody acked it
    me = (await ravi.get("/api/me")).json()
    assert me["firm_stopped"] is False and [a["id"] for a in me["accounts"]] == ["acc-ravi"]


async def test_one_firms_stop_never_touches_another(firms):
    w = firms
    ravi, order = await theirs_running(w)
    ayush = await w.logged_in("ayush")
    r = await ayush.post("/api/firm/stop", json={"reason": "our desk stops"})
    assert r.status_code == 200
    assert {x["account_id"] for x in r.json()["reports"]} == {"acc-ayush", "acc-riya"}
    assert w.platform.firm_switch.engaged and not w.platform.firm_switch_of(ACME).engaged
    assert (await ravi.get("/api/me")).json()["firm_stopped"] is False
    placed = await ravi.post("/api/accounts/acc-ravi/orders", json=ORDER)
    assert placed.json()["status"] != "REJECTED"  # acme still trades
    orders = (await ravi.get("/api/accounts/acc-ravi/orders")).json()
    assert any(o["request"]["client_order_id"] == order and o["status"] != "CANCELLED"
               for o in orders)  # fmt: skip

    r = await ravi.post("/api/firm/stop", json={"reason": "acme stops"})  # their own STOP
    assert {x["account_id"] for x in r.json()["reports"]} == {"acc-ravi"}
    assert w.platform.firm_switch_of(ACME).engaged
    ayush2 = await w.logged_in("ayush")
    assert (await ayush2.post("/api/firm/resume", json={})).status_code == 200  # resumes main…
    assert w.platform.firm_switch_of(ACME).engaged  # …never acme
    w.platform.firm_switch.engage("main again", "ayush")
    ravi2 = await w.logged_in("ravi")
    assert (await ravi2.post("/api/firm/resume", json={})).status_code == 200  # acme resumes…
    assert not w.platform.firm_switch_of(ACME).engaged
    assert w.platform.firm_switch.engaged  # …and main stays stopped


async def test_admin_screens_list_only_their_own_firm(firms):
    w = firms
    ayush = await w.logged_in("ayush")
    users = [u["id"] for u in (await ayush.get("/api/admin/users")).json()]
    accounts = [a["id"] for a in (await ayush.get("/api/admin/accounts")).json()["accounts"]]
    assert sorted(users) == ["ayush", "risk", "riya"]
    assert sorted(accounts) == ["acc-ayush", "acc-riya"]
    # a new user is made in the admin's own firm, and can't be made a trader of acme's account
    made = await ayush.post("/api/admin/users", json={"id": "neo", "name": "Neo",
                                                      "roles": ["trader"]})  # fmt: skip
    assert made.status_code == 201 and w.users.get_user("neo").workspace == "main"
    ravi = await w.logged_in("ravi")
    kim = await ravi.post("/api/admin/users", json={"id": "kim", "name": "Kim", "roles": ["risk"]})
    assert kim.status_code == 201 and w.users.get_user("kim").workspace == ACME  # theirs
    r = await ravi.patch("/api/admin/accounts/acc-ravi", json={"traders": ["tara", "neo"]})
    assert r.status_code == 422 and "no such user: neo" in r.text
    r = await ravi.post("/api/admin/accounts", json={"id": "acc-x", "broker": "paper",
                        "broker_client_id": "P1", "owner": "ayush"})  # fmt: skip
    assert r.status_code in (404, 422) and w.users.get_account("acc-x") is None


def test_ids_are_unique_across_firms(firms):
    w = firms
    with pytest.raises(ValueError, match="already exists"):
        w.users.create_user("ravi", "Other Ravi", PASSWORD, {Role.TRADER})  # in main
    with pytest.raises(ValueError, match="no workspace"):
        w.users.create_user("zed", "Zed", PASSWORD, {Role.TRADER}, workspace="nowhere")


async def test_the_stream_carries_nothing_of_another_firm(tmp_path, nifty_fut):
    """Live server: main's orders, STOP and account bands never reach acme's screens."""
    import asyncio
    import json

    from test_stream import Live, next_of

    server = Live(tmp_path, nifty_fut)
    server.users.create_workspace(ACME, "Acme Capital")
    made = server.users.create_user("ravi", "Ravi", PASSWORD, {Role.ADMIN, Role.TRADER, Role.RISK},
                                    workspace=ACME)  # fmt: skip
    server.secrets["ravi"] = parse_qs(urlparse(made.totp_uri).query)["secret"][0]
    server.users.add_account(BrokerAccount("acc-ravi", "kite", "RV0001", "ravi"))
    account = server.users.get_account("acc-ravi")
    server.platform.add_account(account, FakeBroker(), ids=ClientIds("AC"), algo_types=BUILTIN)
    server.hub.attach(server.platform.accounts["acc-ravi"])
    await server.start()
    try:
        ayush, ravi = await server.session("ayush"), await server.session("ravi")
        async with server.stream(ravi) as theirs, server.stream(ayush) as ours:
            hello = await next_of(theirs, "hello")
            assert hello["accounts"] == ["acc-ravi"]
            await next_of(ours, "hello")
            placed = await ayush.post("/api/accounts/acc-ayush/orders", json=ORDER)
            assert placed.status_code == 201
            await ayush.post("/api/accounts/acc-ayush/stop", json={"reason": "account stop"})
            server.platform.firm_switch.engage("main's firm STOP", "ayush")
            server.alerts.raise_(
                "mtm:acc-ayush:x", "ERROR", "acc-ayush MTM", account_id="acc-ayush"
            )
            assert (await next_of(ours, "kill"))["scope"]  # ours arrive…
            received = []
            try:
                async with asyncio.timeout(1.0):
                    while True:
                        received.append(json.loads(await theirs.recv()))
            except TimeoutError:
                pass
            for message in received:  # …theirs: nothing of main's, ticks aside (#395)
                if message.get("type") == "ticks":
                    continue
                text = json.dumps(message)
                assert "acc-ayush" not in text and "ayush" not in text, message
                assert message.get("type") != "kill", message

            server.platform.firm_switch_of(ACME).engage("acme's STOP", "ravi")
            kill = await next_of(theirs, "kill")
            # to acme's own screens, acme's STOP is simply "the firm" (the screens' firm band)
            assert kill["scope"] == "firm" and kill["engaged"] and kill["reason"] == "acme's STOP"
    finally:
        await server.stop()


def test_only_the_operator_makes_a_firm_from_the_terminal(tmp_path, capsys):
    from laddrix.server.security import SecretBox
    from laddrix.server.users import UserStore
    from laddrix.tools import users as users_tool

    env = {"LADDRIX_SECRET_KEY": SecretBox.new_key(),
           "LADDRIX_USERS_DB": str(tmp_path / "users.db")}  # fmt: skip
    assert users_tool.main(["add-workspace", ACME, "Acme Capital", "--admin", "ravi"], env,
                           lambda: PASSWORD) == 0  # fmt: skip
    assert "Created workspace acme (Acme Capital) with its admin ravi" in capsys.readouterr().out
    users_tool.main(["add-user", "tara", "Tara", "--workspace", ACME], env, lambda: PASSWORD)
    users_tool.main(["add-account", "acc-ravi", "paper", "P1", "--owner", "ravi"], env)
    store = UserStore(tmp_path / "users.db", SecretBox(env["LADDRIX_SECRET_KEY"]))
    assert store.get_user("ravi").roles == {Role.ADMIN} and store.get_user("ravi").workspace == ACME
    assert store.get_user("tara").workspace == ACME
    assert store.get_account("acc-ravi").workspace == ACME  # its owner's firm
    assert sorted(store.workspaces()) == [(ACME, "Acme Capital"), ("main", "Main")]
    with pytest.raises(ValueError, match="already exists"):
        users_tool.main(["add-workspace", ACME, "Again", "--admin", "x"], env, lambda: PASSWORD)


async def test_server_wide_controls_are_the_operators_once_there_are_two_firms(firms):
    ayush = await firms.logged_in("ayush")
    r = await ayush.delete("/api/metrics")
    assert r.status_code == 403 and "only its operator" in r.json()["detail"]


async def test_an_admin_never_sees_another_firms_backtests(tmp_path, nifty_fut):
    """An admin sees their own firm's backtests (#270, #368), never another firm's: Acme's
    admin gets "no backtest", exactly as for one that doesn't exist."""
    from test_backtest import DAY, MODEL, day_ticks

    from laddrix.research.backtest import Backtests

    w = TwoFirms(tmp_path, nifty_fut)
    runs = Backtests(tmp_path / "bt", w.platform.instruments, MODEL,
                     ticks=lambda d: iter(day_ticks(FUT)) if d == DAY else None)  # fmt: skip
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), backtests=runs)
    riya, ayush, ravi = [await w.logged_in(u) for u in ("riya", "ayush", "ravi")]
    body = {"days": [DAY.isoformat()], "algo": "SLICER", "instrument_id": FUT, "side": "BUY",
            "qty": 150, "params": {"slices": 2, "interval_s": 60, "mode": "cross"}}  # fmt: skip
    run = (await riya.post("/api/research/backtests", json=body)).json()["run_id"]
    await runs.settle()
    assert (await ayush.get(f"/api/research/backtests/{run}")).status_code == 200  # main's admin
    assert (await ravi.get("/api/research/backtests")).json() == []  # Acme's admin: not theirs
    other = await ravi.get(f"/api/research/backtests/{run}")
    assert other.status_code == 404 and other.json()["detail"] == f"no backtest {run}"
    for c in (riya, ayush, ravi):
        await c.aclose()
    w.log.close()
