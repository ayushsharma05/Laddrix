"""One person's data: exported for them, erased on request (#367).

export-user ─► profile, accounts, settings, layouts, sessions, alerts, webhook (its key
               named, not given), backtests, every audit entry naming them
erase-user  ─► refused while a server runs, if they own an account, if they are the last
               admin, or without --confirm; else everything above goes except the audit
               trail, which keeps them by id (record keeping)
"""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from mocktrade.alerts.store import AlertStore
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.ops.fence import DataFence
from mocktrade.server import security
from mocktrade.server.security import SecretBox
from mocktrade.server.users import LastAdmin, LoginFailed, UserStore
from mocktrade.store.events import EventLog, verify_chain
from mocktrade.tools import users as users_tool

PASSWORD = "a long enough password"
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


async def world(tmp_path):
    key = SecretBox.new_key()
    store = UserStore(tmp_path / "users.db", SecretBox(key), clock=lambda: NOW)
    secrets = {}
    for user, roles in (("ayush", {Role.ADMIN, Role.TRADER}), ("riya", {Role.TRADER})):
        uri = store.create_user(user, user.title(), PASSWORD, roles).totp_uri
        secrets[user] = parse_qs(urlparse(uri).query)["secret"][0]
    store.add_account(BrokerAccount("acc-1", "paper", "P1", "ayush", frozenset({"riya"})))
    store.set_setting("riya", "theme", json.dumps({"name": "dark"}))
    store.save_layout("riya", "Main", json.dumps({"grid": "riya's"}))
    store.start_session(store.get_user("riya"), device="Firefox", ip="10.0.0.9")
    hooks = AlertStore(tmp_path / "alerts")
    hooks.set_webhook("riya", {"url": "https://hooks.example.com/r", "secret": "whsec-riya-1"})
    hooks.set_webhook("ayush", {"url": "https://hooks.example.com/a", "secret": "whsec-ayush"})
    for run_id, owner in (("bt-1", "riya"), ("bt-2", "ayush")):
        (tmp_path / "backtests" / run_id).mkdir(parents=True)
        (tmp_path / "backtests" / run_id / "run.json").write_text(json.dumps({"owner": owner}))
    log = EventLog(tmp_path / "events.db")
    log.start()
    changed = {"by": "ayush", "user_id": "riya"}
    log.record_note("ADMIN_USER_CHANGED", "-", "ayush changed riya", changed)
    log.record_note("STEP_UP_OK", "-", "riya confirmed", {"user_id": "riya"})
    log.record_note("KILL_NOTE", "acc-1", "riya pressed STOP", {"by": "riya"})  # she did it
    their_order = {"order": {"request": {"user_id": "riya", "client_order_id": "MT1"}}}
    log.record_note("ORDER_NOTE", "acc-1", "MT1: riya's order", their_order)
    log.record_note("STEP_UP_OK", "-", "ayush confirmed", {"user_id": "ayush"})
    await log.flush()
    await log.aclose()
    env = {"MOCKTRADE_SECRET_KEY": key, "MOCKTRADE_USERS_DB": str(tmp_path / "users.db")}
    return store, env, secrets


async def test_an_export_holds_their_data_and_no_secret(tmp_path, capsys):
    store, env, secrets = await world(tmp_path)
    assert users_tool.main(["export-user", "riya"], env) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["user"] == {"id": "riya", "name": "Riya", "roles": ["trader"], "active": True}
    assert data["accounts"] == [{"id": "acc-1", "broker": "paper", "as": "trader"}]
    assert data["settings"] == {"theme": {"name": "dark"}}
    assert data["layouts"] == {"Main": {"grid": "riya's"}}
    assert [(s["device"], s["ip"]) for s in data["sessions"]] == [("Firefox", "10.0.0.9")]
    assert data["webhook"] == {
        "url": "https://hooks.example.com/r",
        "signing_key": "set (not shown)",
    }
    assert data["backtests"] == ["bt-1"]
    assert [e["detail"] for e in data["audit_trail"]] == [
        "ayush changed riya",
        "riya confirmed",
        "riya pressed STOP",
        "MT1: riya's order",
    ]
    for secret in ("whsec-riya-1", secrets["riya"], "argon2", "whsec-ayush"):
        assert secret not in out
    with pytest.raises(ValueError, match="no user ghost"):
        users_tool.main(["export-user", "ghost"], env)


async def test_an_erase_is_refused_until_it_is_safe(tmp_path):
    store, env, _ = await world(tmp_path)
    before = (tmp_path / "users.db").read_bytes()
    with pytest.raises(ValueError, match="repeat the user id"):
        users_tool.main(["erase-user", "riya", "--confirm", "riyaa"], env)
    with pytest.raises(ValueError, match="ayush owns acc-1: give each account another owner"):
        users_tool.main(["erase-user", "ayush", "--confirm", "ayush"], env)
    server = DataFence(tmp_path)
    server.acquire()
    try:
        with pytest.raises(ValueError, match="stop the server first"):
            users_tool.main(["erase-user", "riya", "--confirm", "riya"], env)
    finally:
        server.release()
    with pytest.raises(ValueError, match="no user ghost"):
        users_tool.main(["erase-user", "ghost", "--confirm", "ghost"], env)
    assert (tmp_path / "users.db").read_bytes() == before  # nothing changed
    assert AlertStore(tmp_path / "alerts").path_of("riya").exists()
    assert (tmp_path / "backtests" / "bt-1").exists()

    lone = UserStore(tmp_path / "solo.db", SecretBox(SecretBox.new_key()))
    lone.create_user("boss", "Boss", PASSWORD, {Role.ADMIN})
    with pytest.raises(LastAdmin, match="last active admin"):
        lone.erase_user("boss")


async def test_an_erase_removes_everything_but_the_audit_trail(tmp_path, capsys):
    store, env, secrets = await world(tmp_path)
    assert users_tool.main(["erase-user", "riya", "--confirm", "riya"], env) == 0
    said = capsys.readouterr().out
    assert "Erased riya: alert_files 1, backtests 1, traders_of 1, sessions 1, user_settings 1, " \
           "layouts 1" in said  # fmt: skip
    assert "Kept: their entries in the audit trail" in said

    gone = store.get_user("riya")
    assert (gone.name, gone.roles, gone.active) == ("erased user", frozenset(), False)
    assert store.get_account("acc-1").traders == frozenset()
    assert store.get_setting("riya", "theme") is None and store.layouts("riya") == []
    assert store.own_sessions("riya", None) == []
    with pytest.raises(LoginFailed):  # neither the old password nor the old authenticator
        store.verify_login("riya", PASSWORD, security.totp_code(secrets["riya"], NOW.timestamp()))
    assert not AlertStore(tmp_path / "alerts").path_of("riya").exists()
    assert AlertStore(tmp_path / "alerts").path_of("ayush").exists()  # someone else's: kept
    assert not (tmp_path / "backtests" / "bt-1").exists()
    assert (tmp_path / "backtests" / "bt-2").exists()
    # the audit trail is untouched: still whole, still naming riya by id
    assert verify_chain(tmp_path / "events.db").ok
    assert users_tool.main(["export-user", "riya"], env) == 0
    left = json.loads(capsys.readouterr().out)
    assert len(left["audit_trail"]) == 4 and left["user"]["name"] == "erased user"
    assert store.get_user("ayush").name == "Ayush"  # nobody else touched
