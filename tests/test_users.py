"""The user store details, the Kite daily login, and the users command-line tool."""

import json
import os
import stat
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from laddrix.broker.kite.daily_login import KiteDailyLogin, WrongKiteUser
from laddrix.broker.kite.http import KiteHttp
from laddrix.config import ConfigError, KiteSettings
from laddrix.domain.accounts import BrokerAccount, Role
from laddrix.server import security
from laddrix.server.security import SecretBox
from laddrix.server.users import LoginFailed, UserStore
from laddrix.tools import users as users_tool

PASSWORD = "a long enough password"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path / "users.db", SecretBox(SecretBox.new_key()), clock=lambda: NOW)


def secret_of(uri: str) -> str:
    return parse_qs(urlparse(uri).query)["secret"][0]


def test_users_are_created_once_with_valid_ids(store):
    created = store.create_user("ayush", "Ayush", PASSWORD, {Role.TRADER})
    assert created.user.roles == {Role.TRADER}
    assert store.get_user("ayush") == created.user
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("ayush", "Again", PASSWORD, set())
    with pytest.raises(ValueError, match="letters, digits or dashes"):
        store.create_user("ay ush", "Space", PASSWORD, set())
    assert store.get_user("nobody") is None


def test_secrets_are_encrypted_in_the_database(store):
    created = store.create_user("ayush", "Ayush", PASSWORD, {Role.TRADER})
    raw = (store.path).read_bytes()
    assert secret_of(created.totp_uri).encode() not in raw
    assert PASSWORD.encode() not in raw


def test_accounts_need_an_existing_owner_and_unique_ids(store):
    store.create_user("ayush", "Ayush", PASSWORD, {Role.TRADER})
    store.create_user("riya", "Riya", PASSWORD, {Role.TRADER})
    account = BrokerAccount("acc-1", "kite", "AB1234", "ayush", frozenset({"riya"}))
    store.add_account(account)
    assert store.get_account("acc-1") == account and store.accounts() == [account]
    with pytest.raises(ValueError, match="already exists"):
        store.add_account(account)
    with pytest.raises(ValueError, match="no user"):
        store.add_account(BrokerAccount("acc-2", "kite", "X", "ghost"))
    with pytest.raises(ValueError, match="no such user: nobody"):  # traders must exist (#368)
        store.add_account(BrokerAccount("acc-3", "kite", "Y", "ayush", frozenset({"nobody"})))


def test_a_disabled_user_cannot_log_in(store):
    created = store.create_user("ayush", "Ayush", PASSWORD, {Role.TRADER})
    store.set_active("ayush", False)
    code = security.totp_code(secret_of(created.totp_uri), NOW.timestamp())
    with pytest.raises(LoginFailed, match="wrong username, password or code"):
        store.verify_login("ayush", PASSWORD, code)


def test_unknown_session_tokens_are_nobody(store):
    assert store.session_user(None) is None
    assert store.session_user("made-up-token") is None


# --- Kite daily login --------------------------------------------------------------------------


def kite_app(_account):
    return KiteSettings("key1", "secret1", None)


def session_server(user_id):
    def handler(request: httpx.Request):
        assert request.url.path == "/session/token"
        data = {"user_id": user_id, "access_token": "tok-123"}
        return httpx.Response(200, json={"status": "success", "data": data})

    return lambda api_key: KiteHttp(api_key, transport=httpx.MockTransport(handler))


async def test_daily_login_saves_this_accounts_token_privately(tmp_path):
    started = []

    async def on_session(account, session):
        started.append((account.id, session.user_id))

    login = KiteDailyLogin(
        kite_app, tmp_path, on_session=on_session, http_factory=session_server("AB1234")
    )
    account = BrokerAccount("acc-1", "kite", "AB1234", "ayush")
    assert "state%3Dxyz" in login.login_url(account, "xyz")
    await login.complete(account, "request-token")
    saved = login.token_file(account)
    assert json.loads(saved.read_text())["user_id"] == "AB1234"
    assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600
    assert started == [("acc-1", "AB1234")]


async def test_logging_in_as_the_wrong_zerodha_client_is_refused(tmp_path):
    login = KiteDailyLogin(kite_app, tmp_path, http_factory=session_server("RY5678"))
    account = BrokerAccount("acc-1", "kite", "AB1234", "ayush")
    with pytest.raises(WrongKiteUser, match="as RY5678, but account acc-1 is AB1234"):
        await login.complete(account, "request-token")
    assert not login.token_file(account).exists()


# --- the users tool ------------------------------------------------------------------------------


def test_users_tool_end_to_end(tmp_path, capsys):
    assert users_tool.main(["new-key"], {}) == 0
    key = capsys.readouterr().out.splitlines()[0].split("=", 1)[1]
    env = {"LADDRIX_SECRET_KEY": key, "LADDRIX_USERS_DB": str(tmp_path / "u.db")}
    users_tool.main(
        ["add-user", "ayush", "Ayush", "--roles", "admin,trader"], env, lambda: PASSWORD
    )
    assert "otpauth://totp/Laddrix:ayush" in capsys.readouterr().out
    users_tool.main(["add-account", "acc-1", "kite", "AB1234", "--owner", "ayush"], env)
    users_tool.main(["password", "ayush"], env, lambda: "another long password")
    users_tool.main(["disable", "ayush"], env)
    store = users_tool.store_from_env(env)
    assert store.get_account("acc-1").broker_client_id == "AB1234"
    assert store.get_user("ayush").active is False
    with pytest.raises(ConfigError, match="LADDRIX_SECRET_KEY"):
        users_tool.store_from_env({})


def test_weak_old_hashes_are_upgraded_at_the_next_login(store):
    from argon2 import PasswordHasher

    created = store.create_user("ayush", "Ayush", PASSWORD, {Role.TRADER})
    previous = security.use_hasher(PasswordHasher(time_cost=2, memory_cost=16, parallelism=1))
    try:
        code = security.totp_code(secret_of(created.totp_uri), NOW.timestamp())
        store.verify_login("ayush", PASSWORD, code)
        with store._db() as db:
            stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert not security.needs_rehash(stored) and "t=2" in stored
    finally:
        security.use_hasher(previous)
