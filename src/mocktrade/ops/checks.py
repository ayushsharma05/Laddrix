"""The checks the scheduler runs, each raising or clearing one alert.

kite_login   08:30–15:30, every minute: a kite account without today's login.
             WARN until 09:05, then ERROR ("the market opens in 10 min" / "is open")
prices       09:15–15:30, every 15 s: no new price for PRICES_SILENT_S while the feed
             should be running (Kite logged in, or the simulator)
disk         every 5 min: the data volume nearly full (backups, audit trail)
"""

import math
import shutil
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, time
from pathlib import Path

from mocktrade.domain.accounts import BrokerAccount
from mocktrade.marketdata.service import MarketData
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.scheduler import IST

MARKET_OPEN = time(9, 15)
ESCALATE_AT = time(9, 5)
PRICES_SILENT_S = 60
DISK_WARN_BYTES = 1 << 30  # 1 GB
DISK_ERROR_BYTES = 200 << 20


class Checks:
    def __init__(
        self,
        alerts: Alerts,
        market_data: MarketData,
        accounts: Iterable[BrokerAccount],
        data_dir: Path,
        *,
        broker_status: Callable[[BrokerAccount], str | None] | None = None,
        prices_expected: Callable[[], bool] = lambda: True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free,
    ) -> None:
        self._alerts = alerts
        self._md = market_data
        self._accounts = list(accounts)
        self._data_dir = data_dir
        self._status = broker_status
        self._prices_expected = prices_expected
        self._clock = clock
        self._disk_free = disk_free
        self._ticks_seen = market_data.ticks_received
        self._ticks_changed_at = clock()

    def kite_login(self) -> None:
        if self._status is None:
            return
        now = self._clock().astimezone(IST)
        for account in self._accounts:
            key = f"broker_login:{account.id}"
            if self._status(account) != "login_needed":
                self._alerts.clear(key, f"{account.id}: logged in to {account.broker} for today")
                continue
            who = f"{account.id} ({account.broker_client_id})"
            if now.time() < ESCALATE_AT:
                level, when = "WARN", "the market opens at 09:15"
            elif now.time() < MARKET_OPEN:
                left = datetime.combine(now.date(), MARKET_OPEN, tzinfo=IST) - now
                minutes = math.ceil(left.total_seconds() / 60)
                level, when = "ERROR", f"the market opens in {minutes} min"
            else:
                level, when = "ERROR", "the market is open and this account can't trade"
            self._alerts.raise_(key, level, f"Log in to Kite for {who}: {when}")

    def prices(self) -> None:
        key = "prices"
        now = self._clock()
        if self._md.ticks_received != self._ticks_seen:
            self._ticks_seen = self._md.ticks_received
            self._ticks_changed_at = now
            self._alerts.clear(key, "prices are flowing again")
            return
        if not self._prices_expected():
            self._ticks_changed_at = now  # no feed expected (e.g. not logged in): not silence
            return
        silent = (now - self._ticks_changed_at).total_seconds()
        if silent >= PRICES_SILENT_S:
            self._alerts.raise_(
                key,
                "ERROR",
                "No prices for over a minute during market hours: check the Kite connection "
                "(runbook: websocket down)",
            )

    def disk(self) -> None:
        free = self._disk_free(self._data_dir)
        key = "disk"
        if free < DISK_ERROR_BYTES:
            self._alerts.raise_(key, "ERROR", f"Data disk almost full: {free >> 20} MB free")
        elif free < DISK_WARN_BYTES:
            self._alerts.raise_(key, "WARN", f"Data disk low: {free >> 20} MB free")
        else:
            self._alerts.clear(key, "disk space is fine again")
