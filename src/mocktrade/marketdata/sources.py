"""Where each account's prices come from (#395, docs/design/tenancy.md §4).

Ayush's rule (#278): each trader sees only prices from their own broker subscription. So a
price is never "the server's": it is one subscription's.

    shared   one MarketData for the simulator or a replay: MockTrade's own data, everyone's
    per account (Kite)   each Kite account's ticker feeds its own MarketData: that account's
             risk checks, algos, GTTs, positions and fills read only it
    a paper account      fills against its owner's own Kite account's prices (on a Kite
             server); against the shared simulator otherwise

    a person's screens   prices from one source: their own account's ("owner", the default).
                         Someone with no subscription of their own sees none on a Kite server.
"""

from collections.abc import Iterable

from mocktrade.domain.accounts import BrokerAccount, User
from mocktrade.marketdata.service import MarketData

SHARED = "shared"


class PriceSources:
    def __init__(self, shared: MarketData, *, per_account: bool = False) -> None:
        self.shared = shared  # the simulator / a replay; on a Kite server, nobody's screens
        self.per_account = per_account  # a Kite server: every price is one account's
        self._by_account: dict[str, MarketData] = {}
        self._uses: dict[str, str] = {}  # account id -> the account whose prices it uses

    def register(self, account_id: str, prices: MarketData) -> None:
        """A subscription of its own: this account's broker connection feeds `prices`."""
        self._by_account[account_id] = prices

    def borrow(self, account_id: str, from_account_id: str) -> None:
        """A paper account that fills against another account's prices (its owner's)."""
        self._uses[account_id] = from_account_id

    def has_own(self, account_id: str) -> bool:
        return account_id in self._by_account

    def of(self, account_id: str) -> MarketData:
        """The prices this account trades on."""
        source = self._uses.get(account_id, account_id)
        found = self._by_account.get(source)
        if found is not None:
            return found
        if self.per_account:  # a Kite server never falls back to someone else's prices
            raise LookupError(f"{account_id} has no prices of its own")
        return self.shared

    def all(self) -> list[MarketData]:
        """Every distinct source (each marked, watched and checked on its own)."""
        found = {id(self.shared): self.shared} if not self.per_account else {}
        for prices in self._by_account.values():
            found.setdefault(id(prices), prices)
        return list(found.values())

    def for_user(self, user: User, accounts: Iterable[BrokerAccount]) -> MarketData | None:
        """The prices a person's screens show: the shared source on a simulator or replay;
        on a Kite server, their own first account's subscription, or None (no subscription
        of their own: no prices, #278)."""
        if not self.per_account:
            return self.shared
        for account in sorted(accounts, key=lambda a: a.id):
            if (account.owner_user_id == user.id and account.workspace == user.workspace
                    and account.id in self._by_account):  # fmt: skip
                return self._by_account[account.id]
        return None
