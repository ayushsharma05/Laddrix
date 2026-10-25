"""Manage Laddrix users and accounts from the server's terminal.

    python -m laddrix.tools.users new-key                  # once: the secret key for .env
    python -m laddrix.tools.users add-user ayush "Ayush" --roles admin,trader
    python -m laddrix.tools.users add-workspace acme "Acme Capital" --admin ravi   # a firm (#368)
    python -m laddrix.tools.users add-account acc-ayush kite AB1234 --owner ayush
    python -m laddrix.tools.users password ayush
    python -m laddrix.tools.users disable ayush
    python -m laddrix.tools.users revoke-sessions [--user ayush]   # everyone logs in again
    python -m laddrix.tools.users rotate-key [--check]   # LADDRIX_NEW_SECRET_KEY=... (#364)
    python -m laddrix.tools.users export-user ayush > ayush.json   # their data (#367)
    python -m laddrix.tools.users erase-user ayush --confirm ayush  # server stopped (#367)

add-user prints an otpauth:// link once: open it as a QR code (or paste it) in an
authenticator app. Passwords are typed, never passed on the command line (they'd end
up in the shell history). Keys likewise come from the environment, never the command line.

rotate-key (docs/runbooks/incidents.md, "A credential is stolen"):

    new-key ─► save it in the password manager ─► stop the server
    LADDRIX_SECRET_KEY=<in use> LADDRIX_NEW_SECRET_KEY=<new> users rotate-key
      ─► a server still owns the data folder       ─► refused (it would hold the old key)
      ─► the current key can't open every secret   ─► refused, nothing changed
      ─► every secret re-encrypted, read back, written at once
    ─► put the new key in .env, start the server, then retire the old key
       (keep it beside the backups made before today: they need it)

revoke-sessions works while the server runs: open screens close within seconds.

export-user and erase-user (docs/design/data.md, "One person's data"):

    export ─► their profile, accounts, settings, layouts, sessions, alerts (the webhook's key
              is named, not given), backtests, and every audit entry that names them; JSON
    erase  ─► refused while a server owns the data folder, if they own an account, or if they
              are the last admin; else their alerts and backtests deleted, then their profile
              (sessions, settings, layouts gone; name, password, authenticator replaced)
           ─► the audit trail keeps them, by id: it is kept by law, not by choice
"""

import argparse
import getpass
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from laddrix.alerts.engine import alert_to_dict
from laddrix.alerts.store import AlertStore
from laddrix.config import ConfigError, environment
from laddrix.domain.accounts import BrokerAccount, Role
from laddrix.ops.fence import DataFence, FenceError
from laddrix.server.security import SecretBox
from laddrix.server.users import WRONG_KEY, UserStore
from laddrix.spreads.store import SpreadBook


def ask_password() -> str:
    first = getpass.getpass("New password (12+ characters): ")
    if first != getpass.getpass("Again: "):
        raise ValueError("the two passwords differ")
    return first


def store_from_env(env: dict[str, str]) -> UserStore:
    key = env.get("LADDRIX_SECRET_KEY")
    if not key:
        raise ConfigError("missing LADDRIX_SECRET_KEY (make one with: new-key)")
    path = Path(env.get("LADDRIX_USERS_DB", "data/users.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return UserStore(path, SecretBox(key))


def main(argv: list[str], env: dict[str, str], password=ask_password) -> int:
    parser = argparse.ArgumentParser(prog="laddrix.tools.users")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new-key")
    add = sub.add_parser("add-user")
    add.add_argument("user_id")
    add.add_argument("name")
    add.add_argument("--roles", default="trader", help="comma separated: admin,trader,risk")
    add.add_argument("--workspace", default="main", help="the firm (#368); default: main")
    ws = sub.add_parser("add-workspace", help="a new firm, with its first admin (#368)")
    ws.add_argument("workspace_id")
    ws.add_argument("name")
    ws.add_argument("--admin", required=True, help="its first admin's user id (created now)")
    acc = sub.add_parser("add-account")
    acc.add_argument("account_id")
    acc.add_argument("broker", choices=["kite", "paper"])
    acc.add_argument("broker_client_id")
    acc.add_argument("--owner", required=True)
    acc.add_argument("--traders", default="", help="other user ids allowed to trade it")
    pw = sub.add_parser("password")
    pw.add_argument("user_id")
    off = sub.add_parser("disable")
    off.add_argument("user_id")
    end = sub.add_parser("revoke-sessions")
    end.add_argument("--user", help="only this user's sessions (default: everyone's)")
    out = sub.add_parser("export-user")
    out.add_argument("user_id")
    erase = sub.add_parser("erase-user")
    erase.add_argument("user_id")
    erase.add_argument("--confirm", required=True, help="the user id again: there is no undo")
    rotate = sub.add_parser("rotate-key")
    rotate.add_argument("--check", action="store_true", help="check both keys; change nothing")
    args = parser.parse_args(argv)

    if args.command == "new-key":
        print(f"LADDRIX_SECRET_KEY={SecretBox.new_key()}")
        print("Put this line in the server's .env (never in git). Losing it means")
        print("re-creating every user's authenticator.")
        return 0
    store = store_from_env(env)
    if args.command == "add-user":
        roles = {Role(r.strip()) for r in args.roles.split(",") if r.strip()}
        created = store.create_user(args.user_id, args.name, password(), roles,
                                    workspace=args.workspace)  # fmt: skip
        print(f"Created {created.user.id} with roles {sorted(r.value for r in roles)}.")
        print("Add this to an authenticator app now (it won't be shown again):")
        print(created.totp_uri)
    elif args.command == "add-workspace":
        # only from here: no screen can create a firm or reach another (docs/design/tenancy.md)
        store.create_workspace(args.workspace_id, args.name)
        created = store.create_user(args.admin, args.admin, password(), {Role.ADMIN},
                                    workspace=args.workspace_id)  # fmt: skip
        print(f"Created workspace {args.workspace_id} ({args.name}) with its admin {args.admin}.")
        print("Their admin adds the firm's users and accounts from the Admin screen.")
        print("Add this to the admin's authenticator app now (it won't be shown again):")
        print(created.totp_uri)
    elif args.command == "add-account":
        traders = frozenset(t for t in args.traders.split(",") if t)
        store.add_account(
            BrokerAccount(args.account_id, args.broker, args.broker_client_id, args.owner, traders)
        )
        print(f"Added account {args.account_id} ({args.broker} {args.broker_client_id}).")
    elif args.command == "password":
        store.set_password(args.user_id, password())
        print(f"Password changed for {args.user_id}; their sessions were ended.")
    elif args.command == "disable":
        store.set_active(args.user_id, False)
        print(f"Disabled {args.user_id}; their sessions were ended.")
    elif args.command == "revoke-sessions":
        try:
            ended = store.revoke_sessions(args.user)
        except KeyError:
            raise ValueError(f"no user {args.user}") from None
        who = args.user or "everyone"
        print(f"Ended {ended} session(s) for {who}: open screens close within seconds; "
              "each must log in again (password and authenticator).")  # fmt: skip
    elif args.command == "rotate-key":
        return rotate_key(store, env, check=args.check)
    elif args.command == "export-user":
        print(json.dumps(export_user(store, args.user_id), indent=1, ensure_ascii=False))
    elif args.command == "erase-user":
        if args.confirm != args.user_id:
            raise ValueError("--confirm must repeat the user id: nothing was erased")
        done = erase_user(store, args.user_id)
        print(f"Erased {args.user_id}: " + ", ".join(f"{k} {v}" for k, v in done.items()))
        print("Kept: their entries in the audit trail, by id (record keeping). Backups made "
              "before today still hold them until they age out (14/30 days).")  # fmt: skip
    return 0


def _named(payload: dict, user_id: str) -> bool:
    """An audit row is about this person: they did it, it was done to them, or it is their
    order."""
    request = (payload.get("order") or {}).get("request") or {}
    return user_id in (payload.get("by"), payload.get("user_id"), request.get("user_id"))


def export_user(store: UserStore, user_id: str) -> dict:
    try:
        found = store.export_user(user_id)
    except KeyError:
        raise ValueError(f"no user {user_id}") from None
    data = store.path.parent
    alerts = AlertStore(data / "alerts")
    found["alerts"] = [alert_to_dict(a) for a in alerts.load_all() if a.user_id == user_id]
    hook = alerts.webhook(user_id)
    found["webhook"] = {"url": hook["url"], "signing_key": "set (not shown)"} if hook else None
    spreads = SpreadBook(data / "spreads", lambda _i: None).path_of(user_id)  # #414
    found["spreads"] = (json.loads(spreads.read_text()).get("spreads", [])
                        if spreads is not None and spreads.exists() else [])  # fmt: skip
    found["backtests"] = sorted(run_id for run_id, owner in _runs(data) if owner == user_id)
    found["audit_trail"] = []
    if (data / "events.db").exists():
        db = sqlite3.connect(f"file:{data / 'events.db'}?mode=ro", uri=True)
        try:
            for seq, ts, account, kind, detail, payload in db.execute(
                "SELECT seq, ts, account_id, kind, detail, payload FROM events ORDER BY seq"
            ):
                if _named(json.loads(payload), user_id):
                    found["audit_trail"].append({"seq": seq, "ts": ts, "account": account,
                                                 "kind": kind, "detail": detail})  # fmt: skip
        finally:
            db.close()
    return found


def _runs(data: Path) -> list[tuple[str, str]]:
    """(run id, owner) of every backtest kept (research/backtest.py writes <id>/run.json)."""
    found = []
    for path in sorted((data / "backtests").glob("*/run.json")):
        try:
            found.append((path.parent.name, json.loads(path.read_text()).get("owner", "")))
        except (OSError, ValueError):
            continue  # unreadable: the server skips it too; nobody's
    return found


def erase_user(store: UserStore, user_id: str) -> dict[str, int]:
    data = store.path.parent
    fence = DataFence(data)  # the running server holds alerts in memory: it would write back
    try:
        fence.acquire()
    except FenceError as exc:
        raise ValueError(f"stop the server first: {exc}") from None
    try:
        store.erase_check(user_id)  # every refusal before anything is touched
        done = {"alert_files": 0, "spread_files": 0, "backtests": 0}
        path = AlertStore(data / "alerts").path_of(user_id)
        if path.exists():
            path.unlink()
            done["alert_files"] = 1
        spreads = SpreadBook(data / "spreads", lambda _i: None).path_of(user_id)
        if spreads is not None and spreads.exists():  # their spread definitions (#414)
            spreads.unlink()
            done["spread_files"] = 1
        for run_id, owner in _runs(data):
            if owner == user_id:
                shutil.rmtree(data / "backtests" / run_id)
                done["backtests"] += 1
        return done | store.erase_user(user_id)
    except KeyError:
        raise ValueError(f"no user {user_id}") from None
    finally:
        fence.release()


def rotate_key(store: UserStore, env: dict[str, str], *, check: bool) -> int:
    new = env.get("LADDRIX_NEW_SECRET_KEY", "")
    if not new:
        raise ConfigError("set LADDRIX_NEW_SECRET_KEY to the new key (make one: new-key)")
    if new == env.get("LADDRIX_SECRET_KEY"):
        raise ConfigError("the new key is the key in use: make a new one (new-key)")
    new_box = SecretBox(new)
    if bad := store.check_key():
        raise ValueError(f"the current key can't open the secrets of {', '.join(bad)}: "
                         f"nothing was changed ({WRONG_KEY})")  # fmt: skip
    if check:
        print("Both keys are valid and the current one opens every secret: ready to rotate.")
        return 0
    fence = DataFence(store.path.parent)  # the server holds this while it runs (#273)
    try:
        fence.acquire()
    except FenceError as exc:
        raise ValueError(f"stop the server first: {exc}") from None
    try:
        count = store.rotate_key(new_box)
    finally:
        fence.release()
    print(f"Re-encrypted {count} secret(s) under the new key. Next:")
    print("  1. put LADDRIX_SECRET_KEY=<the new key> in the server's .env and start it;")
    print("  2. log in once to prove it; then retire the old key, but keep it with the")
    print("     backups made before today (a restored backup needs the key of its time).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], environment()))
    except (ValueError, ConfigError) as exc:
        sys.exit(f"error: {exc}")
