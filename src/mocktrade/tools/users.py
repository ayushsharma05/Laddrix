"""Manage MockTrade users and accounts from the server's terminal.

    python -m mocktrade.tools.users new-key                  # once: the secret key for .env
    python -m mocktrade.tools.users add-user ayush "Ayush" --roles admin,trader
    python -m mocktrade.tools.users add-account acc-ayush kite AB1234 --owner ayush
    python -m mocktrade.tools.users password ayush
    python -m mocktrade.tools.users disable ayush

add-user prints an otpauth:// link once: open it as a QR code (or paste it) in an
authenticator app. Passwords are typed, never passed on the command line (they'd end
up in the shell history).
"""

import argparse
import getpass
import sys
from pathlib import Path

from mocktrade.config import ConfigError, environment
from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore


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
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], environment()))
    except (ValueError, ConfigError) as exc:
        sys.exit(f"error: {exc}")
