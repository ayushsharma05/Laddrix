"""Auto kill switch on the daily loss: when today's P&L falls to the limit, stop trading.

The risk gate already refuses opening trades past the daily loss limit. This goes
further, as professional platforms do: it engages the kill switch, which also
cancels every working order (resting orders could still open new exposure).

It fires once per day (India date). After a person resets the switch it stays
quiet, so they can close positions; the risk gate still allows only closing trades.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from mocktrade.domain.money import format_rupees
from mocktrade.risk.kill_switch import KillSwitch, stop_trading

if TYPE_CHECKING:
    from mocktrade.orders.manager import OrderManager

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
AUTO = "auto: daily loss"


class DailyLossWatch:
    def __init__(
        self,
        day_pnl_paise: Callable[[], int],
        limit_paise: int,
        switch: KillSwitch,
        managers: Sequence["OrderManager"],
        *,
        today: Callable[[], date] = lambda: datetime.now(UTC).astimezone(IST).date(),
    ) -> None:
        if limit_paise <= 0:
            raise ValueError("the daily loss limit must be positive")
        self._pnl = day_pnl_paise
        self.limit_paise = limit_paise  # an admin's edit replaces it
        self._switch = switch
        self._managers = tuple(managers)
        self._today = today
        self._fired_on: date | None = None
        self._tasks: set[asyncio.Task] = set()

    def check(self, *_: object) -> bool:
        """Look at today's P&L; engage the switch if the limit is reached. Returns True
        if it fired now. Cheap: call it on every position change (it accepts and
        ignores a listener argument)."""
        pnl = self._pnl()
        if pnl > -self.limit_paise or self._fired_on == self._today():
            return False
        self._fired_on = self._today()
        if self._switch.engaged:
            return False  # someone already stopped trading
        reason = (
            f"daily loss limit reached: {format_rupees(-pnl)} lost "
            f"(limit {format_rupees(self.limit_paise)})"
        )
        log.warning("%s: stopping trading", reason)
        task = asyncio.get_running_loop().create_task(
            stop_trading(self._switch, self._managers, reason, by=AUTO)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def settle(self) -> None:
        """Wait for a stop that is under way (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
