"""Users and broker accounts: who may log in, and whose money an order uses.

A user logs in to Laddrix. A broker account is a real account at a broker
(e.g. one Zerodha client id). A user can own accounts and be allowed to trade
other accounts (e.g. a family member's), but only if they have the trader role.
"""

from dataclasses import dataclass, field
from enum import Enum


class Role(Enum):
    """What a user may do."""

    ADMIN = "admin"  # manage users, accounts, limits; kill switches
    TRADER = "trader"  # place and manage orders and algos
    RISK = "risk"  # read everything, trigger kill switches, no trading


# what each role allows, in words (the admin screen shows these; the checks are in the code)
ROLE_HELP: dict[Role, str] = {
    Role.ADMIN: "manage users, accounts and limits; stop trading",
    Role.TRADER: "place and manage orders and algos on their own and allowed accounts",
    Role.RISK: "see every account and stop trading; no orders",
}


MAIN = "main"  # the workspace of a single-firm server, and of everything from before #368


@dataclass(frozen=True)
class User:
    """Someone who can log in to Laddrix."""

    id: str
    name: str
    roles: frozenset[Role] = field(default_factory=frozenset)
    active: bool = True
    must_change_password: bool = False  # after an admin's reset: nothing else until changed
    workspace: str = MAIN  # the firm it belongs to, for life (#368, docs/design/tenancy.md)

    def has_role(self, role: Role) -> bool:
        return self.active and role in self.roles


@dataclass(frozen=True)
class BrokerAccount:
    """One account at one broker, linked to Laddrix by its owner."""

    id: str  # our id, e.g. "acc-ayush-kite"
    broker: str  # which adapter talks to it: "kite", "paper", ...
    broker_client_id: str  # the broker's id for the account, e.g. a Zerodha client id
    owner_user_id: str
    traders: frozenset[str] = field(default_factory=frozenset)  # other users allowed to trade
    active: bool = True
    workspace: str = MAIN  # the firm it belongs to, its owner's (#368)


def same_workspace(user: User, account: BrokerAccount) -> bool:
    """The first question of every check (#368): another firm's account is never anyone's
    business here, whatever their role."""
    return user.workspace == account.workspace


def can_trade(user: User, account: BrokerAccount) -> bool:
    """True if this user may place orders on this account."""
    if not same_workspace(user, account):
        return False
    if not (user.has_role(Role.TRADER) and account.active):
        return False
    return user.id == account.owner_user_id or user.id in account.traders


def can_manage(user: User, account: BrokerAccount) -> bool:
    """True if this user may work the account's orders even while it is turned off:
    cancel, close, stop. (On a turned-off account the risk checks pass only closing trades.)"""
    if not same_workspace(user, account):
        return False
    if not user.has_role(Role.TRADER):
        return False
    return user.id == account.owner_user_id or user.id in account.traders


def can_view(user: User, account: BrokerAccount) -> bool:
    """True if this user may see this account's orders and positions (also when the
    account is turned off: its positions may still need closing). Admin and risk see every
    account of their own workspace, never another's (#368)."""
    if not user.active or not same_workspace(user, account):
        return False
    if user.has_role(Role.ADMIN) or user.has_role(Role.RISK):
        return True
    return can_manage(user, account)
