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

from mocktrade.domain.accounts import MAIN, BrokerAccount, Role, User
from mocktrade.server import security
from mocktrade.server.security import SecretBox

MAX_FAILURES = 5
LOCK_FOR = timedelta(minutes=15)
SESSION_MAX = timedelta(hours=12)
SESSION_IDLE = timedelta(hours=2)
TOUCH_EVERY = timedelta(minutes=1)  # don't write last_seen on every request
SESSION_ID = 16  # hex characters of the digest that name a session in the list (#363)
STEP_UP_FOR = timedelta(minutes=5)  # a fresh code covers privileged changes this long (#363)

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
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,                 -- one of SETTINGS (keys, theme)
    value TEXT NOT NULL,                -- the screens' own JSON, stored as given
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_id);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,                -- e.g. "main", "acme"
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS layouts (
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    layout TEXT NOT NULL,               -- the screens' own JSON, stored as given
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
"""

# columns added after the first release: (table, column, definition)
_ADDED = (
    ("users", "must_change", "INTEGER NOT NULL DEFAULT 0"),
    # the firm each belongs to (#368): everything from before is the one firm, "main"
    ("users", "workspace", "TEXT NOT NULL DEFAULT 'main'"),
    ("accounts", "workspace", "TEXT NOT NULL DEFAULT 'main'"),
    ("sessions", "stepped_up_at", "TEXT"),  # the last fresh authenticator code (#363)
    ("sessions", "device", "TEXT NOT NULL DEFAULT ''"),  # the browser, as it says (#363)
    ("sessions", "ip", "TEXT NOT NULL DEFAULT ''"),
)

MAX_LAYOUT_BYTES = 256 * 1024
# a user's own screen settings, kept on the server so they follow the user to another machine
SETTINGS = frozenset({"keys", "theme"})  # keyboard shortcuts (K13.4), theme (K13.5)
MAX_SETTING_BYTES = 16 * 1024
MAX_LAYOUTS_PER_USER = 50


class LastAdmin(ValueError):
    """The change would leave no active admin."""


class LoginFailed(Exception):
    """Wrong username, password or code, or a locked user. The message is safe to show."""


@dataclass(frozen=True)
class NewUser:
    user: User
    totp_uri: str  # show once as a QR code; the secret is never shown again


WRONG_KEY = "is MOCKTRADE_SECRET_KEY the key in use?"


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
            db.execute("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
                       (MAIN, "Main", self._clock().isoformat()))  # fmt: skip

    # --- workspaces: one per firm (#368); created only from the server's terminal ----------------

    def create_workspace(self, workspace_id: str, name: str) -> None:
        if not workspace_id.isascii() or not workspace_id.replace("-", "").isalnum():
            raise ValueError("workspace id must be letters, digits or dashes")
        try:
            with self._db() as db:
                db.execute("INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
                           (workspace_id, name, self._clock().isoformat()))  # fmt: skip
        except sqlite3.IntegrityError:
            raise ValueError(f"workspace {workspace_id} already exists") from None

    def workspaces(self) -> list[tuple[str, str]]:
        with self._db() as db:
            return [
                (r["id"], r["name"]) for r in db.execute("SELECT * FROM workspaces ORDER BY id")
            ]

    # --- users -------------------------------------------------------------------------------

    def create_user(
        self, user_id: str, name: str, password: str, roles: set[Role], *,
        must_change: bool = False, workspace: str = MAIN,
    ) -> NewUser:  # fmt: skip
        """`must_change`: the password is a one-time one (an admin's); changed at first login.
        `workspace`: the firm, for life (#368). User ids are unique across all workspaces."""
        if not user_id.isascii() or not user_id.replace("-", "").isalnum():
            raise ValueError("user id must be letters, digits or dashes")
        if workspace not in {w for w, _ in self.workspaces()}:
            raise ValueError(f"no workspace {workspace}")
        secret = security.new_totp_secret()
        password_hash = security.hash_password(password)
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO users (id, name, password_hash, totp_secret, roles, must_change, "
                    "workspace) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        name,
                        password_hash,
                        self._box.seal(secret),
                        _roles(roles),
                        int(must_change),
                        workspace,
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"user {user_id} already exists") from None
        user = User(user_id, name, frozenset(roles), must_change_password=must_change,
                    workspace=workspace)  # fmt: skip
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
        owner = self.get_user(account.owner_user_id)
        if owner is None:
            raise ValueError(f"no user {account.owner_user_id}")
        # the account is its owner's firm's; its traders must be of that firm too (#368)
        self._same_firm(account.traders, owner.workspace)
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO accounts (id, broker, broker_client_id, owner_user_id, traders, "
                    "active, workspace) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        account.id,
                        account.broker,
                        account.broker_client_id,
                        account.owner_user_id,
                        ",".join(sorted(account.traders)),
                        int(account.active),
                        owner.workspace,
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
                # only users of the account's own firm (#368): another firm's id is unknown here
                known = {r["id"] for r in db.execute("SELECT id FROM users WHERE workspace = ?",
                                                     (before.workspace,))}  # fmt: skip
                unknown = sorted(set(traders) - known)
                if unknown:
                    raise ValueError(f"no such user: {', '.join(unknown)}")
                traders = set(traders) - {before.owner_user_id}  # the owner trades it anyway
            after = BrokerAccount(
                before.id, before.broker, before.broker_client_id, before.owner_user_id,
                frozenset(traders) if traders is not None else before.traders,
                active if active is not None else before.active, before.workspace,
            )  # fmt: skip
            db.execute(
                "UPDATE accounts SET traders = ?, active = ? WHERE id = ?",
                (",".join(sorted(after.traders)), int(after.active), account_id),
            )
        return before, after

    def _same_firm(self, user_ids, workspace: str) -> None:
        with self._db() as db:
            known = {r["id"] for r in db.execute("SELECT id FROM users WHERE workspace = ?",
                                                 (workspace,))}  # fmt: skip
        unknown = sorted(set(user_ids) - known)
        if unknown:
            raise ValueError(f"no such user: {', '.join(unknown)}")

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

    def start_session(self, user: User, *, device: str = "", ip: str = "",
                      stepped_up: bool = False) -> str:  # fmt: skip
        """A new session for a user who just logged in. Returns the cookie value. A login
        that just checked the authenticator counts as a step-up (`stepped_up`)."""
        token = security.new_session_token()
        now = self._clock().isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, last_seen, "
                "stepped_up_at, device, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (security.token_digest(token), user.id, now, now, now if stepped_up else None,
                 device[:200], ip[:64]),
            )  # fmt: skip
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

    # --- one person's data: export and erase (#367) ---------------------------------------------

    def export_user(self, user_id: str) -> dict:
        """What this store holds about one person, for them to keep (docs/design/data.md).
        Secrets are named, never given: the password hash and authenticator secret are
        credentials, not information about the person."""
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            accounts = [_account(r) for r in db.execute("SELECT * FROM accounts ORDER BY id")]
            mine = (user_id,)
            settings = db.execute("SELECT name, value FROM user_settings WHERE user_id = ? "
                                  "ORDER BY name", mine).fetchall()  # fmt: skip
            layouts = db.execute("SELECT name, layout FROM layouts WHERE user_id = ? "
                                 "ORDER BY name", mine).fetchall()  # fmt: skip
        user = _user(row)
        return {
            "user": {"id": user.id, "name": user.name, "roles": sorted(r.value for r in user.roles),
                     "active": user.active},
            "accounts": [{"id": a.id, "broker": a.broker, "as": "owner"
                          if a.owner_user_id == user_id else "trader"}
                         for a in accounts
                         if a.owner_user_id == user_id or user_id in a.traders],
            "settings": {r["name"]: json.loads(r["value"]) for r in settings},
            "layouts": {r["name"]: json.loads(r["layout"]) for r in layouts},
            "sessions": self.own_sessions(user_id, None),
            "held_but_not_given": ["password (only a one-way hash is stored)",
                                   "authenticator secret (encrypted)"],
        }  # fmt: skip

    def erase_check(self, user_id: str) -> None:
        """Raise what erase_user would refuse with (KeyError, ValueError, LastAdmin)."""
        with self._db() as db:
            self._erase_refusal(db, user_id)

    def _erase_refusal(self, db: sqlite3.Connection, user_id: str) -> None:
        if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise KeyError(user_id)
        owned = [r["id"] for r in db.execute(
            "SELECT id FROM accounts WHERE owner_user_id = ?", (user_id,))]  # fmt: skip
        if owned:
            raise ValueError(f"{user_id} owns {', '.join(owned)}: give each account another "
                             "owner first (nothing was erased)")  # fmt: skip
        admins = [r["id"] for r in db.execute("SELECT id, roles FROM users WHERE active = 1")
                  if Role.ADMIN.value in r["roles"].split(",")]  # fmt: skip
        if admins == [user_id]:
            raise LastAdmin(f"{user_id} is the last active admin: nothing was erased")

    def erase_user(self, user_id: str) -> dict[str, int]:
        """Remove what this store holds about one person. Their id stays, as a pseudonym:
        the audit trail is kept (record keeping, docs/design/data.md) and names them by id.

            owns an account ─► refused (hand it to someone else first)
            the last active admin ─► refused
            removed from every account's traders; sessions, settings, layouts deleted;
            name replaced, password and authenticator replaced by random ones, turned off
        """
        with self._lock, self._db() as db:
            self._erase_refusal(db, user_id)
            done = {"traders_of": 0}
            for account in [_account(r) for r in db.execute("SELECT * FROM accounts")]:
                if user_id in account.traders:
                    left = ",".join(sorted(account.traders - {user_id}))
                    db.execute("UPDATE accounts SET traders = ? WHERE id = ?", (left, account.id))
                    done["traders_of"] += 1
            for table in ("sessions", "user_settings", "layouts"):
                done[table] = db.execute(f"DELETE FROM {table} WHERE user_id = ?",
                                         (user_id,)).rowcount  # fmt: skip
            db.execute(
                "UPDATE users SET name = 'erased user', roles = '', active = 0, "
                "password_hash = ?, totp_secret = ?, failures = 0, locked_until = NULL, "
                "must_change = 0 WHERE id = ?",
                (security.hash_password(security.new_temporary_password() * 2),
                 self._box.seal(security.new_totp_secret()), user_id),
            )  # fmt: skip
        return done

    # --- step-up and a user's own sessions (#363) ----------------------------------------------

    def step_up(self, token: str, code: str) -> None:
        """A fresh authenticator code for this session: privileged changes are allowed for
        STEP_UP_FOR. A wrong code counts like a failed login: after MAX_FAILURES the user is
        locked and every one of their sessions ends, so a stolen session can't guess codes.
        Raises LoginFailed."""
        digest = security.token_digest(token)
        now = self._clock()
        with self._lock:
            with self._db() as db:
                row = db.execute(
                    "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = ?", (digest,)).fetchone()  # fmt: skip
            if row is None or not row["active"]:
                raise LoginFailed("please log in")
            step = security.match_totp(self._box.open(row["totp_secret"]), code,
                                       now.timestamp(), row["last_totp_step"])  # fmt: skip
            with self._db() as db:  # commit before raising: an error inside rolls back
                if step is None:
                    failures = row["failures"] + 1
                    if failures >= MAX_FAILURES:
                        db.execute("UPDATE users SET failures = 0, locked_until = ? WHERE id = ?",
                                   ((now + LOCK_FOR).isoformat(), row["id"]))  # fmt: skip
                        db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
                    else:
                        db.execute("UPDATE users SET failures = ? WHERE id = ?",
                                   (failures, row["id"]))  # fmt: skip
                else:
                    db.execute("UPDATE users SET failures = 0, last_totp_step = ? WHERE id = ?",
                               (step, row["id"]))  # fmt: skip
                    db.execute("UPDATE sessions SET stepped_up_at = ? WHERE token_hash = ?",
                               (now.isoformat(), digest))  # fmt: skip
        if step is None:
            raise LoginFailed("wrong code")

    def stepped_up(self, token: str | None) -> bool:
        """True if this session gave a fresh authenticator code in the last STEP_UP_FOR."""
        if not token:
            return False
        with self._db() as db:
            row = db.execute("SELECT stepped_up_at FROM sessions WHERE token_hash = ?",
                             (security.token_digest(token),)).fetchone()  # fmt: skip
        if row is None or row["stepped_up_at"] is None:
            return False
        return self._clock() - datetime.fromisoformat(row["stepped_up_at"]) <= STEP_UP_FOR

    def own_sessions(self, user_id: str, token: str | None) -> list[dict]:
        """A user's own sessions, newest first. `id` is a short public name for each (a
        prefix of the stored digest: it can't be turned back into a cookie)."""
        mine = security.token_digest(token) if token else ""
        with self._db() as db:
            rows = db.execute(
                "SELECT token_hash, created_at, last_seen, device, ip FROM sessions "
                "WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()  # fmt: skip
        return [{"id": r["token_hash"][:SESSION_ID], "created_at": r["created_at"],
                 "last_seen": r["last_seen"], "device": r["device"], "ip": r["ip"],
                 "current": r["token_hash"] == mine} for r in rows]  # fmt: skip

    def end_own_session(self, user_id: str, session_id: str) -> bool:
        """End one of the user's own sessions by its public id. False if none matches."""
        if len(session_id) != SESSION_ID or not all(c in "0123456789abcdef" for c in session_id):
            return False
        with self._lock, self._db() as db:
            return db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash LIKE ?",
                              (user_id, session_id + "%")).rowcount > 0  # fmt: skip

    def end_session(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (security.token_digest(token),)
                )

    # --- a user's own screen settings (shortcuts, theme) -------------------------------------

    def get_setting(self, user_id: str, name: str) -> str | None:
        if name not in SETTINGS:
            raise ValueError(f"no setting called {name!r}")
        with self._db() as db:
            row = db.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND name = ?", (user_id, name)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, user_id: str, name: str, value: str | None) -> None:
        """Store (or with None, forget: back to the defaults) one setting, as given."""
        if name not in SETTINGS:
            raise ValueError(f"no setting called {name!r}")
        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM user_settings WHERE user_id = ? AND name = ?",
                           (user_id, name))  # fmt: skip
                return
            if len(value.encode()) > MAX_SETTING_BYTES:
                raise ValueError(
                    f"the {name} setting is larger than {MAX_SETTING_BYTES // 1024} KB"
                )
            db.execute(
                "INSERT INTO user_settings (user_id, name, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id, name) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (user_id, name, value, self._clock().isoformat()),
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

    # --- the secret key and sessions in an incident (#364) -------------------------------------

    def check_key(self, box: SecretBox | None = None) -> list[str]:
        """The users whose authenticator secret this key (default: the current one) can't open.
        Empty means the key opens everything."""
        box = box or self._box
        with self._db() as db:
            rows = db.execute("SELECT id, totp_secret FROM users ORDER BY id").fetchall()
        bad = []
        for row in rows:
            try:
                box.open(row["totp_secret"])
            except ValueError:
                bad.append(row["id"])
        return bad

    def rotate_key(self, new: SecretBox) -> int:
        """Re-encrypt every secret under `new`, all at once or not at all.

            open each with the current key ─► any fails ─► nothing changed (ValueError)
            seal each with the new key, open it again with the new key (proves it reads back)
            write them all in one transaction ─► after that, only the new key opens them

        The caller keeps the current key until the server runs with the new one (and keeps it
        with the backups made before: a restored backup needs the key of its time)."""
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # no other writer between reading and writing
            rows = db.execute("SELECT id, totp_secret FROM users ORDER BY id").fetchall()
            sealed = []
            for row in rows:
                try:
                    plain = self._box.open(row["totp_secret"])
                except ValueError:
                    raise ValueError(f"the current key can't open {row['id']}'s secret: "
                                     f"nothing was changed ({WRONG_KEY})") from None  # fmt: skip
                again = new.seal(plain)
                if new.open(again) != plain:  # never write what can't be read back
                    raise ValueError(f"{row['id']}'s secret didn't read back: nothing was changed")
                sealed.append((again, row["id"]))
            db.executemany("UPDATE users SET totp_secret = ? WHERE id = ?", sealed)
        self._box = new
        return len(sealed)

    def revoke_sessions(self, user_id: str | None = None) -> int:
        """End every session (or one user's) now: each must log in again. Open live
        connections close within seconds (the stream checks, server/stream.py)."""
        with self._lock, self._db() as db:
            if user_id is None:
                return db.execute("DELETE FROM sessions").rowcount
            if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise KeyError(user_id)
            return db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount

    def session_digests(self) -> set[str]:
        """The digests of the sessions that exist: one cheap read for every open stream."""
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT token_hash FROM sessions")}

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
    return User(row["id"], row["name"], roles, bool(row["active"]), bool(row["must_change"]),
                row["workspace"])  # fmt: skip


def _account(row: sqlite3.Row) -> BrokerAccount:
    traders = frozenset(t for t in row["traders"].split(",") if t)
    return BrokerAccount(
        row["id"],
        row["broker"],
        row["broker_client_id"],
        row["owner_user_id"],
        traders,
        bool(row["active"]),
        row["workspace"],
    )
