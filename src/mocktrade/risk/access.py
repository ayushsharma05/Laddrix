"""Who may still trade, checked when an order actually goes, not only when it was asked (#366).

A request is checked at the door (server/context.py). But some actions happen later, for the
user who started them: an algo's child orders, a GTT the broker fires, a basket's legs. If an
admin removes a trader from an account, turns the account off or disables the user in the
meantime, those must stop adding risk at once:

    every new order or change ─► AccessGate: the user may still trade this account?
                                  no ─► refused, saying why; closing orders (reduce-only,
                                        the exit desk) and cancels still go
    an algo's next child        ─► the same question first; no ─► the algo stops, saying why
    a GTT                       ─► the same question on each poll; no ─► deleted at the broker
                                   unless it only reduces (orders/gtt.py, like STOP)

The answer comes from the user store each time (`Platform.access`), so a change takes effect
on the next order, with no restart and no cache to forget.
"""

from collections.abc import Callable
from typing import Any

from mocktrade.domain.accounts import can_manage

Access = Callable[[str, str], str | None]  # (user id, account id) -> why not, or None


def no_check(_user_id: str, _account_id: str) -> str | None:
    return None


def trading_rights(users: Any) -> Access:
    """The server's check, from the user store (server/users.py). The same rule as an order
    from the screens (`can_manage`: an active trader of this account); a turned-off account
    is the risk gate's business (closing orders only), as it is for the screens."""

    def check(user_id: str, account_id: str) -> str | None:
        user, account = users.get_user(user_id), users.get_account(account_id)
        if user is None or account is None:
            return f"{user_id} is not a user of {account_id}"
        if not user.active:
            return f"{user_id} is disabled"
        if not can_manage(user, account):
            return f"{user_id} may no longer trade {account_id}"
        return None

    return check


class AccessGate:
    """An order gate (orders/gate.py) for one account."""

    def __init__(self, account_id: str, access: Callable[[], Access]) -> None:
        self._account_id = account_id
        self._access = access  # read at each call: the platform's current check

    def _why(self, user_id: str) -> str | None:
        return self._access()(user_id, self._account_id)

    async def admit_new(self, request) -> str | None:
        if request.reduce_only or request.forced_close:
            return None  # closing never needs trading rights: getting out stays possible
        return self._why(request.user_id)

    async def admit_modify(self, order, new) -> str | None:
        if new.reduce_only or new.forced_close:
            return None
        return self._why(order.request.user_id)

    async def admit_cancel(self, order) -> str | None:
        return None  # cancelling only ever reduces risk
