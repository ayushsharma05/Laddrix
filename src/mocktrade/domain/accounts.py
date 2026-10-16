"""Users and broker accounts: who may log in, and whose money an order uses.

A user logs in to MockTrade. A broker account is a real account at a broker
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


@dataclass(frozen=True)
class User:
    """Someone who can log in to MockTrade."""

    id: str
    name: str
    roles: frozenset[Role] = field(default_factory=frozenset)
    active: bool = True
    must_change_password: bool = False  # after an admin's reset: nothing else until changed

    def has_role(self, role: Role) -> bool:
        return self.active and role in self.roles


@dataclass(frozen=True)
class BrokerAccount:
    """One account at one broker, linked to MockTrade by its owner."""

    id: str  # our id, e.g. "acc-ayush-kite"
    broker: str  # which adapter talks to it: "kite", "paper", ...
    broker_client_id: str  # the broker's id for the account, e.g. a Zerodha client id
    owner_user_id: str
    traders: frozenset[str] = field(default_factory=frozenset)  # other users allowed to trade
    active: bool = True


def can_trade(user: User, account: BrokerAccount) -> bool:
    """True if this user may place orders on this account."""
    if not (user.has_role(Role.TRADER) and account.active):
        return False
    return user.id == account.owner_user_id or user.id in account.traders


def can_manage(user: User, account: BrokerAccount) -> bool:
    """True if this user may work the account's orders even while it is turned off:
    cancel, close, stop. (On a turned-off account the risk checks pass only closing trades.)"""
    if not user.has_role(Role.TRADER):
        return False
    return user.id == account.owner_user_id or user.id in account.traders


def can_view(user: User, account: BrokerAccount) -> bool:
    """True if this user may see this account's orders and positions (also when the
    account is turned off: its positions may still need closing)."""
    if not user.active:
        return False
    if user.has_role(Role.ADMIN) or user.has_role(Role.RISK):
        return True
    return can_manage(user, account)
