"""Manage MockTrade users and accounts from the server's terminal.

    python -m mocktrade.tools.users new-key                  # once: the secret key for .env
    python -m mocktrade.tools.users add-user ayush "Ayush" --roles admin,trader
    python -m mocktrade.tools.users add-account acc-ayush kite AB1234 --owner ayush
    python -m mocktrade.tools.users password ayush
    python -m mocktrade.tools.users disable ayush
    python -m mocktrade.tools.users revoke-sessions [--user ayush]   # everyone logs in again
    python -m mocktrade.tools.users rotate-key [--check]   # MOCKTRADE_NEW_SECRET_KEY=... (#364)

add-user prints an otpauth:// link once: open it as a QR code (or paste it) in an
authenticator app. Passwords are typed, never passed on the command line (they'd end
up in the shell history). Keys likewise come from the environment, never the command line.

rotate-key (docs/runbooks/incidents.md, "A credential is stolen"):

    new-key ─► save it in the password manager ─► stop the server
    MOCKTRADE_SECRET_KEY=<in use> MOCKTRADE_NEW_SECRET_KEY=<new> users rotate-key
      ─► a server still owns the data folder       ─► refused (it would hold the old key)
      ─► the current key can't open every secret   ─► refused, nothing changed
      ─► every secret re-encrypted, read back, written at once
    ─► put the new key in .env, start the server, then retire the old key
       (keep it beside the backups made before today: they need it)

revoke-sessions works while the server runs: open screens close within seconds.
"""

import argparse
import getpass
import sys
from pathlib import Path

from mocktrade.config import ConfigError, environment
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.ops.fence import DataFence, FenceError
from mocktrade.server.security import SecretBox
from mocktrade.server.users import WRONG_KEY, UserStore


def ask_password() -> str:
    first = getpass.getpass("New password (12+ characters): ")
    if first != getpass.getpass("Again: "):
        raise ValueError("the two passwords differ")
    return first


def store_from_env(env: dict[str, str]) -> UserStore:
    key = env.get("MOCKTRADE_SECRET_KEY")
    if not key:
        raise ConfigError("missing MOCKTRADE_SECRET_KEY (make one with: new-key)")
    path = Path(env.get("MOCKTRADE_USERS_DB", "data/users.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return UserStore(path, SecretBox(key))


def main(argv: list[str], env: dict[str, str], password=ask_password) -> int:
    parser = argparse.ArgumentParser(prog="mocktrade.tools.users")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new-key")
    add = sub.add_parser("add-user")
    add.add_argument("user_id")
    add.add_argument("name")
    add.add_argument("--roles", default="trader", help="comma separated: admin,trader,risk")
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
    rotate = sub.add_parser("rotate-key")
    rotate.add_argument("--check", action="store_true", help="check both keys; change nothing")
    args = parser.parse_args(argv)

    if args.command == "new-key":
        print(f"MOCKTRADE_SECRET_KEY={SecretBox.new_key()}")
        print("Put this line in the server's .env (never in git). Losing it means")
        print("re-creating every user's authenticator.")
        return 0
    store = store_from_env(env)
    if args.command == "add-user":
        roles = {Role(r.strip()) for r in args.roles.split(",") if r.strip()}
        created = store.create_user(args.user_id, args.name, password(), roles)
        print(f"Created {created.user.id} with roles {sorted(r.value for r in roles)}.")
        print("Add this to an authenticator app now (it won't be shown again):")
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
    return 0


def rotate_key(store: UserStore, env: dict[str, str], *, check: bool) -> int:
    new = env.get("MOCKTRADE_NEW_SECRET_KEY", "")
    if not new:
        raise ConfigError("set MOCKTRADE_NEW_SECRET_KEY to the new key (make one: new-key)")
    if new == env.get("MOCKTRADE_SECRET_KEY"):
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
    print("  1. put MOCKTRADE_SECRET_KEY=<the new key> in the server's .env and start it;")
    print("  2. log in once to prove it; then retire the old key, but keep it with the")
    print("     backups made before today (a restored backup needs the key of its time).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], environment()))
    except (ValueError, ConfigError) as exc:
        sys.exit(f"error: {exc}")
