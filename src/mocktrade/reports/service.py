"""Reports for the screens: a day's report (kept after the close, or a live preview), P&L history,
and the 16:00 job that keeps each account's report.

    16:00 IST, trading days ─► close_of_day ─► build_day(final, closing prices) ─► ReportStore
    GET a day   ─► kept? the kept one ─ today? a live preview ─ else: rebuilt, without prices
    GET history ─► kept reports in the range ─► by day / instrument / strategy
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from mocktrade.reports.daily import IST, build_day, report_to_dict
from mocktrade.reports.history import pnl_history
from mocktrade.reports.store import ReportStore

log = logging.getLogger(__name__)


class ReportError(Exception):
    """Can't make that report (said in words to the trader)."""


class Reports:
    def __init__(
        self,
        platform,  # Platform
        store: ReportStore | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        is_trading_day: Callable[[date], bool] = lambda d: d.weekday() < 5,
    ) -> None:
        self._platform = platform
        self.store = store
        self._clock = clock
        self._is_trading_day = is_trading_day

    def today(self) -> date:
        return self._clock().astimezone(IST).date()

    def day(self, account_id: str, day: date) -> dict[str, Any]:
        if day > self.today():
            raise ReportError(f"{day} hasn't happened yet")
        if self.store is not None and (kept := self.store.load(account_id, day)) is not None:
            return kept
        today = day == self.today()
        return self._build(account_id, day, prices=today, final=False)

    def kept_days(self, account_id: str) -> list[date]:
        return self.store.days(account_id) if self.store is not None else []

    def history(self, account_id: str, by: str, first: date, last: date) -> dict[str, Any]:
        if first > last:
            raise ReportError("the start is after the end")
        if (last - first).days > 366:
            raise ReportError("at most a year at a time")
        reports = self.store.between(account_id, first, last) if self.store is not None else []
        days = [d for d in _dates(first, min(last, self.today())) if self._is_trading_day(d)]
        try:
            return pnl_history(reports, by, first, last, trading_days=days)
        except ValueError as exc:
            raise ReportError(str(exc)) from None

    async def close_of_day(self) -> None:
        """The scheduler's job after the close: keep every account's report for today."""
        if self.store is None:
            return
        day = self.today()
        for account_id in list(self._platform.accounts):
            try:
                report = self._build(account_id, day, prices=True, final=True)
                path = self.store.save(report)
                log.info("end-of-day report kept: %s", path)
                if self._platform.event_log is not None:
                    t = report["totals"]
                    self._platform.event_log.record_note(
                        "REPORT_KEPT", account_id,
                        f"end-of-day report {day}: gross {t['gross_paise'] / 100:.2f}, "
                        f"charges {t['charges_paise'] / 100:.2f}, net {t['net_paise'] / 100:.2f}",
                    )  # fmt: skip
            except Exception:
                log.exception("end-of-day report for %s failed", account_id)

    def _build(self, account_id: str, day: date, *, prices: bool, final: bool) -> dict[str, Any]:
        platform = self._platform
        if platform.event_log is None:
            raise ReportError("reports need the audit trail")
        report = build_day(
            account_id, day, platform.event_log, platform.instruments.get,
            platform.charges_model, prices=self._marks(account_id) if prices else None,
            now=self._clock(), final=final,
        )  # fmt: skip
        return report_to_dict(report)

    def _marks(self, account_id: str):
        """Open positions are valued at the account's own subscription's prices (#396), never
        another's; an account with none (no longer running on a Kite server) gets no marks."""
        try:
            return self._platform.prices.of(account_id).latest
        except LookupError:
            return None


def _dates(first: date, last: date) -> list[date]:
    from datetime import timedelta

    out, d = [], first
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out
