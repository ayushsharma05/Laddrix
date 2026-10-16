"""Users, broker accounts and login sessions, in a small SQLite file.

Login = username + password + 6-digit authenticator code, checked together. A
failure never says which part was wrong. Five failures lock the user for 15 minutes.
Sessions last at most 12 hours and end after 2 idle hours; logout ends them at once.

Admin changes (K13.1) keep at least one active admin: the last one can't lose the role or be
turned off, checked under the store's lock so two admins can't remove each other at once.
A password reset gives a one-time password the user must change before anything else.
"""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mocktrade.domain.accounts import BrokerAccount, Role, User
from mocktrade.server import security
from mocktrade.server.security import SecretBox

MAX_FAILURES = 5
LOCK_FOR = timedelta(minutes=15)
SESSION_MAX = timedelta(hours=12)
SESSION_IDLE = timedelta(hours=2)
TOUCH_EVERY = timedelta(minutes=1)  # don't write last_seen on every request

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    totp_secret TEXT NOT NULL,          -- encrypted with MOCKTRADE_SECRET_KEY
    roles TEXT NOT NULL,                -- comma separated Role values
    active INTEGER NOT NULL DEFAULT 1,
    failures INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_totp_step INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    broker_client_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    traders TEXT NOT NULL DEFAULT '',   -- comma separated user ids
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS account_limits (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    limits TEXT NOT NULL,               -- the limits spec's flat names, as JSON
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_id);
CREATE TABLE IF NOT EXISTS layouts (
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    layout TEXT NOT NULL,               -- the screens' own JSON, stored as given
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
"""

# columns added after the first release: (table, column, definition)
_ADDED = (("users", "must_change", "INTEGER NOT NULL DEFAULT 0"),)

MAX_LAYOUT_BYTES = 256 * 1024
MAX_LAYOUTS_PER_USER = 50


class LastAdmin(ValueError):
    """The change would leave no active admin."""


class LoginFailed(Exception):
    """Wrong username, password or code, or a locked user. The message is safe to show."""


@dataclass(frozen=True)
class NewUser:
    user: User
    totp_uri: str  # show once as a QR code; the secret is never shown again


class UserStore:
    def __init__(
        self,
        path: str | Path,
        box: SecretBox,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = Path(path)
        self._box = box
        self._clock = clock
        self._lock = threading.Lock()  # one writer at a time (requests run in threads)
        with self._db() as db:
            db.executescript(_SCHEMA)
            for table, column, definition in _ADDED:  # an older file: add what it lacks
                have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # --- users -------------------------------------------------------------------------------

    def create_user(
        self, user_id: str, name: str, password: str, roles: set[Role], *, must_change: bool = False
    ) -> NewUser:
        """`must_change`: the password is a one-time one (an admin's); changed at first login."""
        if not user_id.isascii() or not user_id.replace("-", "").isalnum():
            raise ValueError("user id must be letters, digits or dashes")
        secret = security.new_totp_secret()
        password_hash = security.hash_password(password)
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO users (id, name, password_hash, totp_secret, roles, must_change) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        name,
                        password_hash,
                        self._box.seal(secret),
                        _roles(roles),
                        int(must_change),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"user {user_id} already exists") from None
        user = User(user_id, name, frozenset(roles), must_change_password=must_change)
        return NewUser(user, security.totp_uri(secret, user_id))

    def get_user(self, user_id: str) -> User | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def set_password(self, user_id: str, password: str) -> None:
        password_hash = security.hash_password(password)
        with self._db() as db:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # log out everywhere

    def set_active(self, user_id: str, active: bool) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
            if not active:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def users(self) -> list[dict]:
        """Everyone, for the admin screen: who, roles, on/off, and whether login is locked."""
        now = self._clock()
        with self._db() as db:
            rows = db.execute("SELECT * FROM users ORDER BY id").fetchall()
            sessions = dict(db.execute(
                "SELECT user_id, COUNT(*) FROM sessions GROUP BY user_id").fetchall())  # fmt: skip
        out = []
        for r in rows:
            locked = r["locked_until"] and datetime.fromisoformat(r["locked_until"]) > now
            out.append({"user": _user(r), "locked_until": r["locked_until"] if locked else None,
                        "failures": r["failures"],
                        "sessions": sessions.get(r["id"], 0)})  # fmt: skip
        return out

    def update_user(
        self,
        user_id: str,
        *,
        name: str | None = None,
        roles: set[Role] | None = None,
        active: bool | None = None,
    ) -> tuple[User, User]:
        """Change a user; (before, after). Turning off ends their sessions. Refuses (LastAdmin)
        to leave no active admin."""
        if name is not None and not 1 <= len(name.strip()) <= 80:
            raise ValueError("name: 1-80 characters")
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            before = _user(row)
            after = User(
                before.id,
                name.strip() if name is not None else before.name,
                frozenset(roles) if roles is not None else before.roles,
                active if active is not None else before.active,
                before.must_change_password,
            )
            if before.has_role(Role.ADMIN) and not after.has_role(Role.ADMIN):
                others = [_user(r) for r in db.execute("SELECT * FROM users WHERE id != ?",
                                                       (user_id,))]  # fmt: skip
                if not any(u.has_role(Role.ADMIN) for u in others):
                    raise LastAdmin(f"{user_id} is the last active admin: make another admin "
                                    "first")  # fmt: skip
            db.execute(
                "UPDATE users SET name = ?, roles = ?, active = ? WHERE id = ?",
                (after.name, _roles(set(after.roles)), int(after.active), user_id),
            )
            if not after.active:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return before, after

    def unlock(self, user_id: str) -> bool:
        """Clear failed logins; True if the user was locked or had failures."""
        with self._db() as db:
            cursor = db.execute(
                "UPDATE users SET failures = 0, locked_until = NULL WHERE id = ? "
                "AND (failures > 0 OR locked_until IS NOT NULL)", (user_id,))  # fmt: skip
        return cursor.rowcount > 0

    def reset_password(self, user_id: str) -> str:
        """A one-time password (shown once to the admin); the user must change it after the
        next login, and is logged out everywhere now."""
        temporary = security.new_temporary_password()
        password_hash = security.hash_password(temporary)
        with self._db() as db:
            cursor = db.execute(
                "UPDATE users SET password_hash = ?, must_change = 1, failures = 0, "
                "locked_until = NULL WHERE id = ?", (password_hash, user_id))  # fmt: skip
            if cursor.rowcount == 0:
                raise KeyError(user_id)
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return temporary

    def reset_authenticator(self, user_id: str) -> str:
        """A new authenticator secret (a lost phone); returns its link, shown once. The old
        codes stop working and the user is logged out everywhere."""
        secret = security.new_totp_secret()
        with self._db() as db:
            cursor = db.execute(
                "UPDATE users SET totp_secret = ?, last_totp_step = 0 WHERE id = ?",
                (self._box.seal(secret), user_id))  # fmt: skip
            if cursor.rowcount == 0:
                raise KeyError(user_id)
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return security.totp_uri(secret, user_id)

    def change_own_password(self, user_id: str, old: str, new: str, keep_token: str) -> None:
        """The user's own change: the old password first, the new one strong and different.
        Ends their other sessions (keeps `keep_token`, the one asking)."""
        security.check_password_strength(new)
        if new == old:
            raise ValueError("the new password must differ from the old one")
        with self._lock, self._db() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or not security.verify_password(row["password_hash"], old):
                raise ValueError("the current password is wrong")
            db.execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?",
                       (security.hash_password(new), user_id))  # fmt: skip
            db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                       (user_id, security.token_digest(keep_token)))  # fmt: skip

    def verify_login(self, user_id: str, password: str, code: str) -> User:
        """Check all three together. Raises LoginFailed with one generic message."""
        now = self._clock()
        with self._lock:
            with self._db() as db:
                row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            password_ok = security.verify_password(row["password_hash"] if row else None, password)
            if row is None:
                raise LoginFailed("wrong username, password or code")
            if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
                raise LoginFailed("too many failed attempts: try again in 15 minutes")
            step = None
            if password_ok and row["active"]:
                secret = self._box.open(row["totp_secret"])
                step = security.match_totp(secret, code, now.timestamp(), row["last_totp_step"])
            if step is None:
                failures = row["failures"] + 1
                locked = (now + LOCK_FOR).isoformat() if failures >= MAX_FAILURES else None
                with self._db() as db:  # commit BEFORE raising: an error inside rolls back
                    db.execute(
                        "UPDATE users SET failures = ?, locked_until = ? WHERE id = ?",
                        (0 if locked else failures, locked, user_id),
                    )
                raise LoginFailed("wrong username, password or code")
            with self._db() as db:
                new_hash = None
                if security.needs_rehash(row["password_hash"]):
                    new_hash = security.hash_password(password)
                db.execute(
                    "UPDATE users SET failures = 0, locked_until = NULL, last_totp_step = ?, "
                    "password_hash = COALESCE(?, password_hash) WHERE id = ?",
                    (step, new_hash, user_id),
                )
        return _user(row)

    # --- broker accounts -----------------------------------------------------------------------

    def add_account(self, account: BrokerAccount) -> None:
        # the id names files (MTM rules, broker tokens, reports): letters, digits, dashes only
        if not account.id.isascii() or not account.id.replace("-", "").isalnum():
            raise ValueError("account id must be letters, digits or dashes")
        if self.get_user(account.owner_user_id) is None:
            raise ValueError(f"no user {account.owner_user_id}")
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO accounts (id, broker, broker_client_id, owner_user_id, traders, "
                    "active) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        account.id,
                        account.broker,
                        account.broker_client_id,
                        account.owner_user_id,
                        ",".join(sorted(account.traders)),
                        int(account.active),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"account {account.id} already exists") from None

    def update_account(
        self, account_id: str, *, traders: set[str] | None = None, active: bool | None = None
    ) -> tuple[BrokerAccount, BrokerAccount]:
        """Who else may trade it, and on/off; (before, after). Traders must be users."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise KeyError(account_id)
            before = _account(row)
            if traders is not None:
                known = {r["id"] for r in db.execute("SELECT id FROM users")}
                unknown = sorted(set(traders) - known)
                if unknown:
                    raise ValueError(f"no such user: {', '.join(unknown)}")
                traders = set(traders) - {before.owner_user_id}  # the owner trades it anyway
            after = BrokerAccount(
                before.id, before.broker, before.broker_client_id, before.owner_user_id,
                frozenset(traders) if traders is not None else before.traders,
                active if active is not None else before.active,
            )  # fmt: skip
            db.execute(
                "UPDATE accounts SET traders = ?, active = ? WHERE id = ?",
                (",".join(sorted(after.traders)), int(after.active), account_id),
            )
        return before, after

    def account_limits(self, account_id: str) -> dict | None:
        """The saved limits (the spec's flat names), or None: the defaults."""
        with self._db() as db:
            row = db.execute(
                "SELECT limits FROM account_limits WHERE account_id = ?", (account_id,)
            ).fetchone()
        return json.loads(row["limits"]) if row else None

    def set_account_limits(self, account_id: str, limits: dict, by: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO account_limits (account_id, limits, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET "
                "limits = excluded.limits, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (account_id, json.dumps(limits, sort_keys=True), self._clock().isoformat(), by),
            )

    def get_account(self, account_id: str) -> BrokerAccount | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _account(row) if row else None

    def accounts(self) -> list[BrokerAccount]:
        with self._db() as db:
            return [_account(r) for r in db.execute("SELECT * FROM accounts ORDER BY id")]

    # --- sessions ------------------------------------------------------------------------------

    def start_session(self, user: User) -> str:
        """A new session for a user who just logged in. Returns the cookie value."""
        token = security.new_session_token()
        now = self._clock().isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (security.token_digest(token), user.id, now, now),
            )
        return token

    def session_user(self, token: str | None) -> User | None:
        """The logged-in user for a cookie value, or None (expired, idle or unknown)."""
        if not token:
            return None
        digest = security.token_digest(token)
        now = self._clock()
        with self._db() as db:
            row = db.execute(
                "SELECT s.created_at, s.last_seen, u.* FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            created = datetime.fromisoformat(row["created_at"])
            seen = datetime.fromisoformat(row["last_seen"])
            if now - created > SESSION_MAX or now - seen > SESSION_IDLE or not row["active"]:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                return None
            if now - seen > TOUCH_EVERY:
                db.execute(
                    "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
                    (now.isoformat(), digest),
                )
        return _user(row)

    def end_session(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (security.token_digest(token),)
                )

    # --- saved workspace layouts --------------------------------------------------------------

    def layouts(self, user_id: str) -> list[tuple[str, str]]:
        """(name, updated_at) of each saved layout, newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT name, updated_at FROM layouts WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [(r["name"], r["updated_at"]) for r in rows]

    def get_layout(self, user_id: str, name: str) -> str | None:
        with self._db() as db:
            row = db.execute(
                "SELECT layout FROM layouts WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
        return row["layout"] if row else None

    def save_layout(self, user_id: str, name: str, layout: str) -> None:
        if not name or len(name) > 40 or not name.replace(" ", "").replace("-", "").isalnum():
            raise ValueError("layout name: 1-40 letters, digits, spaces or dashes")
        if len(layout.encode()) > MAX_LAYOUT_BYTES:
            raise ValueError(f"layout is larger than {MAX_LAYOUT_BYTES // 1024} KB")
        with self._lock, self._db() as db:
            exists = db.execute(
                "SELECT 1 FROM layouts WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
            count = db.execute(
                "SELECT COUNT(*) FROM layouts WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            if not exists and count >= MAX_LAYOUTS_PER_USER:
                raise ValueError(f"at most {MAX_LAYOUTS_PER_USER} saved layouts")
            db.execute(
                "INSERT INTO layouts (user_id, name, layout, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id, name) DO UPDATE SET layout = excluded.layout, "
                "updated_at = excluded.updated_at",
                (user_id, name, layout, self._clock().isoformat()),
            )

    def delete_layout(self, user_id: str, name: str) -> bool:
        with self._db() as db:
            cursor = db.execute(
                "DELETE FROM layouts WHERE user_id = ? AND name = ?", (user_id, name)
            )
        return cursor.rowcount > 0

    # --- inside -----------------------------------------------------------------------------

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


def _roles(roles: set[Role]) -> str:
    return ",".join(sorted(r.value for r in roles))


def _user(row: sqlite3.Row) -> User:
    roles = frozenset(Role(r) for r in row["roles"].split(",") if r)
    return User(row["id"], row["name"], roles, bool(row["active"]), bool(row["must_change"]))


def _account(row: sqlite3.Row) -> BrokerAccount:
    traders = frozenset(t for t in row["traders"].split(",") if t)
    return BrokerAccount(
        row["id"],
        row["broker"],
        row["broker_client_id"],
        row["owner_user_id"],
        traders,
        bool(row["active"]),
    )
