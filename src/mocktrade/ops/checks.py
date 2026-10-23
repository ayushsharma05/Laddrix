"""The checks the scheduler runs, each raising or clearing one alert.

kite_login   08:30–15:30, every minute: a kite account without today's login.
             WARN until 09:05, then ERROR ("the market opens in 10 min" / "is open")
prices       09:15–15:30, every 15 s: no new price for PRICES_SILENT_S while the feed
             should be running (Kite logged in, or the simulator). On a Kite server each
             subscription is checked on its own (#396), its band shown to that account's viewers
disk         every 5 min: the data volume nearly full (backups, audit trail)
"""

import math
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
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


@dataclass
class PriceFeed:
    """One subscription's prices, checked for silence on its own (#396)."""

    prices: MarketData
    account_id: str | None = None  # None: the server's own feed (the simulator)
    expected: Callable[[], bool] = lambda: True  # e.g. its Kite login is live
    seen: int = field(default=0, init=False)
    changed_at: datetime | None = field(default=None, init=False)

    @property
    def key(self) -> str:
        return "prices" if self.account_id is None else f"prices:{self.account_id}"


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
        feeds: Iterable[PriceFeed] | None = None,  # each subscription's own (#396)
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free,
    ) -> None:
        self._alerts = alerts
        self._accounts = list(accounts)
        self._data_dir = data_dir
        self._status = broker_status
        self._clock = clock
        self._disk_free = disk_free
        self._feeds = list(feeds) if feeds is not None else [
            PriceFeed(market_data, expected=prices_expected)]  # fmt: skip
        for feed in self._feeds:
            feed.seen, feed.changed_at = feed.prices.ticks_received, clock()

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
            self._alerts.raise_(key, level, f"Log in to Kite for {who}: {when}",
                                account_id=account.id)  # fmt: skip

    def prices(self) -> None:
        for feed in self._feeds:
            self._check_feed(feed)

    def _check_feed(self, feed: PriceFeed) -> None:
        now = self._clock()
        whose = f"{feed.account_id}: " if feed.account_id is not None else ""
        if feed.prices.ticks_received != feed.seen:
            feed.seen, feed.changed_at = feed.prices.ticks_received, now
            self._alerts.clear(feed.key, f"{whose}prices are flowing again")
            return
        if not feed.expected():
            feed.changed_at = now  # no feed expected (e.g. not logged in): not silence
            return
        if (now - feed.changed_at).total_seconds() >= PRICES_SILENT_S:
            self._alerts.raise_(
                feed.key,
                "ERROR",
                f"{whose}No prices for over a minute during market hours: check the Kite "
                "connection (runbook: websocket down)",
                account_id=feed.account_id,  # one firm's subscription: its viewers' band
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


def audit_log(alerts: Alerts, log) -> None:
    """The audit log can't write, or has fallen behind (#265): new orders are being refused,
    so say so on every screen, at any hour; clear it once writing again."""
    problem = log.problem
    if problem is not None:
        text = (f"{problem}: new orders are refused until it writes again (cancels and "
                "forced closes still go). Check the disk.")  # fmt: skip
        alerts.raise_("audit_log", "ERROR", text)
    else:
        alerts.clear("audit_log", "the audit log is writing again: orders accepted")


class PriceOverload:
    """Every-tick readers (algos, the paper broker) fell behind and ticks were dropped (#267):
    a WARN band while it keeps happening, cleared once a check finds no new drops."""

    def __init__(self, alerts: Alerts, *sources: MarketData) -> None:
        self._alerts = alerts
        self._sources = sources  # every subscription's (#396): one server, one CPU
        self._seen = self._dropped()

    def _dropped(self) -> int:
        return sum(md.dropped for md in self._sources)

    def check(self) -> None:
        dropped = self._dropped() - self._seen
        self._seen += dropped
        if dropped > 0:
            self._alerts.raise_("price_overload", "WARN", f"prices: {dropped} ticks were "
                                "dropped since the last check because algos or paper fills "
                                "fell behind; the server is overloaded")  # fmt: skip
        else:
            self._alerts.clear("price_overload", "prices: keeping up again")
