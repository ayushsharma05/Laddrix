"""Rotating the secret key and ending every session, for a stolen credential (#364).

rotate-key: every authenticator secret re-encrypted under a new key, all at once or not at
            all; refused while a server owns the data folder or if the key in use is wrong
revoke-sessions: every session (or one user's) ended now; open streams close in seconds
"""

import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest
from test_stream import Live, next_of
from test_users import PASSWORD, secret_of
from websockets.exceptions import ConnectionClosed

from mocktrade.config import ConfigError
from mocktrade.domain.accounts import Role
from mocktrade.ops.fence import DataFence
from mocktrade.server import security
from mocktrade.server.security import SecretBox
from mocktrade.server.stream import CLOSE_NOT_LOGGED_IN
from mocktrade.server.users import UserStore
from mocktrade.tools import users as users_tool

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def three_users(tmp_path, key):
    store = UserStore(tmp_path / "users.db", SecretBox(key), clock=lambda: NOW)
    uris = {u: store.create_user(u, u.title(), PASSWORD, {Role.TRADER}).totp_uri
            for u in ("ayush", "ravi", "riya")}  # fmt: skip
    return store, {u: secret_of(uri) for u, uri in uris.items()}


def raw(store):
    with sqlite3.connect(store.path) as db:
        return dict(db.execute("SELECT id, totp_secret FROM users"))


def can_log_in(store, user, secret, at=NOW):
    code = security.totp_code(secret, at.timestamp())
    return store.verify_login(user, PASSWORD, code).id == user


def test_rotation_re_encrypts_every_secret_and_logins_still_work(tmp_path):
    old, new = SecretBox.new_key(), SecretBox.new_key()
    store, secrets = three_users(tmp_path, old)
    before = raw(store)
    assert store.rotate_key(SecretBox(new)) == 3
    after = raw(store)
    assert all(after[u] != before[u] for u in before)  # every one changed
    assert store.check_key(SecretBox(old)) == ["ayush", "ravi", "riya"]  # the old key: useless
    assert store.check_key() == []
    fresh = UserStore(store.path, SecretBox(new), clock=lambda: NOW)  # as after a restart
    assert all(can_log_in(fresh, u, s) for u, s in secrets.items())  # same authenticator apps


def test_a_wrong_current_key_changes_nothing(tmp_path):
    store, _ = three_users(tmp_path, SecretBox.new_key())
    before = raw(store)
    wrong = UserStore(store.path, SecretBox(SecretBox.new_key()))
    with pytest.raises(ValueError, match="can't open ayush's secret: nothing was changed"):
        wrong.rotate_key(SecretBox(SecretBox.new_key()))
    assert raw(store) == before


class Breaks(SecretBox):
    """A new key that fails on the second secret, or reads back something else."""

    def __init__(self, key, how):
        super().__init__(key)
        self.how, self.sealed = how, 0

    def seal(self, plain):
        self.sealed += 1
        if self.how == "seal" and self.sealed == 2:
            raise OSError("disk went away")
        return super().seal(plain)

    def open(self, sealed):
        return "garbage" if self.how == "read-back" else super().open(sealed)


@pytest.mark.parametrize("how", ["seal", "read-back"])
def test_a_failure_part_way_changes_nothing(tmp_path, how):
    store, secrets = three_users(tmp_path, SecretBox.new_key())
    before = raw(store)
    with pytest.raises((OSError, ValueError)):
        store.rotate_key(Breaks(SecretBox.new_key(), how))
    assert raw(store) == before  # all or nothing: never half under one key, half the other
    assert can_log_in(store, "ravi", secrets["ravi"])  # the old key still works


def env_for(tmp_path, key, new=None):
    env = {"MOCKTRADE_SECRET_KEY": key, "MOCKTRADE_USERS_DB": str(tmp_path / "users.db")}
    return env | ({"MOCKTRADE_NEW_SECRET_KEY": new} if new else {})


def test_the_tool_checks_first_and_refuses_while_a_server_runs(tmp_path, capsys):
    old, new = SecretBox.new_key(), SecretBox.new_key()
    store, secrets = three_users(tmp_path, old)
    before = raw(store)
    with pytest.raises(ConfigError, match="set MOCKTRADE_NEW_SECRET_KEY"):
        users_tool.main(["rotate-key"], env_for(tmp_path, old))
    with pytest.raises(ConfigError, match="the key in use"):
        users_tool.main(["rotate-key"], env_for(tmp_path, old, old))
    with pytest.raises(ValueError, match="secrets of ayush, ravi, riya"):  # a wrong key in .env
        users_tool.main(["rotate-key"], env_for(tmp_path, SecretBox.new_key(), new))
    assert users_tool.main(["rotate-key", "--check"], env_for(tmp_path, old, new)) == 0
    assert "ready to rotate" in capsys.readouterr().out
    server = DataFence(tmp_path)  # a running server owns the folder
    server.acquire()
    try:
        with pytest.raises(ValueError, match="stop the server first"):
            users_tool.main(["rotate-key"], env_for(tmp_path, old, new))
    finally:
        server.release()
    assert raw(store) == before  # none of that changed anything

    assert users_tool.main(["rotate-key"], env_for(tmp_path, old, new)) == 0
    out = capsys.readouterr().out
    assert "Re-encrypted 3 secret(s)" in out and "backups made before today" in out
    assert new not in out and old not in out  # keys are never printed
    fresh = UserStore(store.path, SecretBox(new), clock=lambda: NOW)
    assert can_log_in(fresh, "riya", secrets["riya"])
    after = DataFence(tmp_path)  # the tool let go of the folder: the server can start
    after.acquire()
    after.release()


def test_sessions_end_for_everyone_or_one_user(tmp_path, capsys):
    key = SecretBox.new_key()
    store, _ = three_users(tmp_path, key)
    tokens = {u: store.start_session(store.get_user(u)) for u in ("ayush", "ravi", "riya")}
    assert store.revoke_sessions("ravi") == 1
    assert store.session_user(tokens["ravi"]) is None
    assert store.session_user(tokens["ayush"]).id == "ayush"
    with pytest.raises(KeyError):
        store.revoke_sessions("ghost")
    assert users_tool.main(["revoke-sessions"], env_for(tmp_path, key)) == 0
    assert "Ended 2 session(s) for everyone" in capsys.readouterr().out
    assert all(store.session_user(t) is None for t in tokens.values())
    with pytest.raises(ValueError, match="no user ghost"):
        users_tool.main(["revoke-sessions", "--user", "ghost"], env_for(tmp_path, key))


@pytest.fixture
async def live(tmp_path, nifty_fut):
    server = Live(tmp_path, nifty_fut)
    await server.start()
    yield server
    await server.stop()


async def test_open_streams_close_within_seconds_of_a_revoke(live):
    live.hub._revoked_s = 0.05  # in production 2 s; the full check stays at a minute
    assert live.hub._recheck_s == 60.0
    client = await live.session()
    other = await live.session("riya")
    async with live.stream(client) as ws, live.stream(other) as ws2:
        await next_of(ws, "hello")
        await next_of(ws2, "hello")
        live.users.revoke_sessions("ayush")  # as the tool does, from another process
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                await asyncio.wait_for(ws.recv(), 2)
        assert closed.value.rcvd.code == CLOSE_NOT_LOGGED_IN
        assert live.hub.connections == 1  # riya's stream stays open
    await client.aclose()
    await other.aclose()


def test_a_backup_never_carries_a_session_so_a_restore_brings_none_back(tmp_path):
    from mocktrade.tools.backup import backup_one

    store, _ = three_users(tmp_path, SecretBox.new_key())
    token = store.start_session(store.get_user("ayush"))
    copy = tmp_path / "copy" / "users.db"
    copy.parent.mkdir()
    backup_one(store.path, copy)
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (3,)  # the rest is there
    assert security.token_digest(token).encode() not in copy.read_bytes()  # not even a trace
    assert store.session_user(token).id == "ayush"  # the live server's sessions untouched
