"""The kill switch: stop everything now.

Engaging it blocks new orders and modifications (cancels always pass) and cancels
every working order of the accounts it covers. It stays engaged until a person
resets it. One switch can cover a whole firm and another a single account; an
order is blocked if any switch covering it is engaged.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from laddrix.orders.manager import OrderManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KillState:
    engaged: bool
    reason: str = ""
    by: str = ""  # who engaged or reset it (a user id, or "auto: daily loss")
    at: datetime | None = None


class KillSwitch:
    def __init__(
        self, name: str, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.name = name  # e.g. "firm" or "account acc-1"
        self._clock = clock
        self._state = KillState(engaged=False)
        self._listeners: list[Callable[[str, KillState], None]] = []

    @property
    def state(self) -> KillState:
        return self._state

    @property
    def engaged(self) -> bool:
        return self._state.engaged

    def engage(self, reason: str, by: str) -> bool:
        """Engage. Returns False if it already was (the first reason is kept)."""
        if self._state.engaged:
            return False
        self._state = KillState(True, reason, by, self._clock())
        self._notify()
        return True

    def reset(self, by: str) -> bool:
        """Allow trading again. Returns False if it wasn't engaged."""
        if not self._state.engaged:
            return False
        self._state = KillState(False, "", by, self._clock())
        self._notify()
        return True

    def on_change(self, listener: Callable[[str, KillState], None]) -> None:
        """Call `listener(name, state)` whenever the switch changes (audit log, screens,
        and the code that cancels working orders)."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener(self.name, self._state)
            except Exception:  # a broken screen must never stop the kill switch
                log.exception("kill switch listener failed")


async def stop_trading(
    switch: KillSwitch, managers: Sequence["OrderManager"], reason: str, by: str
) -> dict[str, list[tuple[str, str]]]:
    """Engage the switch, then cancel every working order of the given accounts.

    The switch is engaged FIRST, so no new order can slip in while the cancels go out.
    Returns, per account, the orders whose cancel failed (client id, reason) so a
    person can act on them; an empty dict means every cancel is on its way.
    """
    switch.engage(reason, by)
    failures = {}
    for manager in managers:
        failed = await manager.cancel_all()
        if failed:
            failures[manager.account_id] = failed
    return failures
