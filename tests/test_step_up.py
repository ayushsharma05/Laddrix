"""Step-up for privileged changes and a user's own session list (#363).

    a privileged change ─► this session gave a fresh authenticator code in the last 5 min?
                            no ─► 428 "step-up: ..." ─► POST /api/me/step-up {code} ─► again
    STOP, Flatten, cancels, tightening a limit, turning something off ─► never asked

A stolen session (the cookie only) can't loosen limits, add traders, reset logins or resume
the firm; guessing codes locks the user and ends every session.
"""

from datetime import timedelta

from test_server import ORDER, PASSWORD, World

from laddrix.domain.accounts import Role
from laddrix.server.users import MAX_FAILURES, STEP_UP_FOR

COOKIE = "__Host-mt_session"  # ServerSettings.cookie_name (secure)


def stolen(w, user_id="ayush"):
    """A client holding only someone's cookie, as a thief would (no code was given)."""
    client = w.client()
    client.cookies.set(COOKIE, w.users.start_session(w.users.get_user(user_id)))
    return client


async def loosen(client, lots=50):
    limits = (await client.get("/api/admin/accounts/acc-riya/limits")).json()["limits"]
    return await client.put("/api/admin/accounts/acc-riya/limits",
                            json={"limits": {**limits, "max_order_lots": lots}})  # fmt: skip


async def test_a_stolen_session_cant_loosen_limits_without_the_code(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    thief = stolen(w)
    r = await loosen(thief)
    assert r.status_code == 428
    assert r.json()["detail"] == "step-up: enter a fresh authenticator code to loosen limits"
    limits = (await thief.get("/api/admin/accounts/acc-riya/limits")).json()["limits"]
    assert limits["max_order_lots"] == 10  # unchanged
    dry = await thief.put("/api/admin/accounts/acc-riya/limits?dry_run=true",
                          json={"limits": {**limits, "max_order_lots": 50}})  # fmt: skip
    assert dry.status_code == 200  # a preview changes nothing: never asked
    tighter = await loosen(thief, lots=5)
    assert tighter.status_code == 200 and tighter.json()["applied"]  # tightening: never asked

    r = await thief.post("/api/me/step-up", json={"code": w.code("ayush")})
    assert r.status_code == 204
    assert (await loosen(thief)).status_code == 200  # with the code: done
    w.clock.now += STEP_UP_FOR + timedelta(seconds=1)
    assert (await loosen(thief, lots=60)).status_code == 428  # a step-up doesn't last
    await w.log.flush()
    kinds = [e.kind for e in w.log.events()]
    assert "STEP_UP_OK" in kinds


async def test_each_privileged_change_asks_and_each_emergency_action_never_does(
    tmp_path, nifty_fut
):
    w = World(tmp_path, nifty_fut)
    thief = stolen(w)
    asked = [
        await thief.patch("/api/admin/accounts/acc-riya", json={"traders": ["ayush"]}),
        await thief.post("/api/admin/users/riya/reset-password", json={}),
        await thief.post("/api/admin/users/riya/reset-authenticator", json={}),
        await thief.patch("/api/admin/users/riya", json={"roles": ["admin", "trader"]}),
        await thief.post(
            "/api/admin/users", json={"id": "mallory", "name": "M", "roles": ["admin"]}
        ),  # fmt: skip
        await thief.post(
            "/api/admin/accounts",
            json={"id": "acc-new", "broker": "paper", "broker_client_id": "P9", "owner": "ayush"},
        ),  # fmt: skip
    ]
    assert [r.status_code for r in asked] == [428] * len(asked), [r.text for r in asked]
    assert w.users.get_user("riya").roles == {Role.TRADER}  # nothing was changed
    assert w.users.get_user("mallory") is None
    assert w.users.get_account("acc-riya").traders == frozenset()

    # reducing risk is never blocked
    off = await thief.patch("/api/admin/accounts/acc-riya", json={"active": False})
    assert off.status_code == 200
    on = await thief.patch("/api/admin/accounts/acc-riya", json={"active": True})
    assert on.status_code == 428  # turning it back on adds risk again
    user_off = await thief.patch("/api/admin/users/riya", json={"active": False})
    assert user_off.status_code == 200
    placed = await thief.post("/api/accounts/acc-ayush/orders", json=ORDER)
    assert placed.status_code == 201  # trading itself is the session's business
    oid = placed.json()["request"]["client_order_id"]
    assert (await thief.delete(f"/api/accounts/acc-ayush/orders/{oid}")).status_code in (200, 202)
    assert (await thief.post("/api/firm/stop", json={"reason": "x"})).status_code == 200
    r = await thief.post("/api/firm/resume", json={})
    assert r.status_code == 428
    assert r.json()["detail"].endswith("resume the whole firm")
    await thief.post("/api/me/step-up", json={"code": w.code("ayush")})
    assert (await thief.post("/api/firm/resume", json={})).status_code == 200


async def test_a_login_counts_as_a_step_up(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    admin = await w.logged_in("ayush")  # the login just checked the code
    assert (await loosen(admin)).status_code == 200


async def test_guessing_codes_locks_the_user_and_ends_every_session(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    thief = stolen(w)
    other = stolen(w)  # the real user's own session, elsewhere
    for _ in range(MAX_FAILURES):
        r = await thief.post("/api/me/step-up", json={"code": "000000"})
        assert r.status_code == 403, r.text
    assert (await thief.get("/api/me")).status_code == 401  # every session ended
    assert (await other.get("/api/me")).status_code == 401
    login = {"username": "ayush", "password": PASSWORD, "code": w.code("ayush")}
    good = await w.client().post("/api/session", json=login)
    assert good.status_code == 401 and "15 minutes" in good.json()["detail"]  # locked
    await w.log.flush()
    assert [e.kind for e in w.log.events()].count("STEP_UP_FAILED") == MAX_FAILURES


async def test_a_code_works_once(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    thief = stolen(w)
    code = w.code("ayush")
    assert (await thief.post("/api/me/step-up", json={"code": code})).status_code == 204
    second = stolen(w)
    assert (await second.post("/api/me/step-up", json={"code": code})).status_code == 403


async def test_users_see_their_own_sessions_and_end_any_of_them(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    mine = await w.logged_in("ayush")
    laptop = await w.logged_in("ayush")
    riyas = await w.logged_in("riya")
    seen = (await mine.get("/api/me/sessions")).json()
    assert len(seen) == 2 and sum(s["current"] for s in seen) == 1
    assert {"id", "created_at", "last_seen", "device", "ip", "current"} == set(seen[0])
    assert all(s["ip"] and s["device"] for s in seen)
    other = next(s for s in seen if not s["current"])
    theirs = (await riyas.get("/api/me/sessions")).json()[0]["id"]
    assert (await mine.delete(f"/api/me/sessions/{theirs}")).status_code == 404  # not mine
    assert (await riyas.get("/api/me")).status_code == 200
    assert (await mine.delete("/api/me/sessions/%25")).status_code == 404  # no wildcards
    assert (await mine.delete(f"/api/me/sessions/{other['id']}")).status_code == 204
    assert (await laptop.get("/api/me")).status_code == 401  # that one is gone
    assert (await mine.get("/api/me")).status_code == 200  # this one stays
    assert len((await mine.get("/api/me/sessions")).json()) == 1
