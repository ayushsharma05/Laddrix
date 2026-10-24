"""Run jobs at India times on trading days.

    scheduler.daily("08:30", refresh_instruments)                 # once each trading day
    scheduler.every(30, check_prices, between=("09:15", "15:30"))  # every 30 s in that window

Times are India times (the exchange's), whatever the server's own time zone. The clock is
read again after every sleep, so a long sleep, a laptop waking up or a clock correction
never makes a job run twice or be skipped silently: a daily job missed by more than its
grace period is skipped (and logged), never run late at the wrong moment.
Trading days are Monday to Friday minus the listed exchange holidays.

Jobs never hold each other up (#274): each due job runs as a task of its own. `run_due` waits
up to WAIT_S for them, then goes on; a job still running when it is due again is skipped (and
logged), never started twice. So the 16:00 reports can't delay the 5 s protection guard.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
WAIT_S = 0.5  # how long run_due waits for the jobs it started before going on

IST = ZoneInfo("Asia/Kolkata")
Job = Callable[[], Awaitable[None] | None]


def parse_hhmm(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


@dataclass
class _Daily:
    at: time
    job: Job
    name: str
    last_day: date | None = None


@dataclass
class _Every:
    seconds: float
    job: Job
    name: str
    start: time
    end: time
    next_at: datetime | None = None
    any_day: bool = False  # also on weekends and holidays (a safety warning: positions stay open)


@dataclass
class Scheduler:
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    holidays: frozenset[date] = frozenset()
    grace: timedelta = timedelta(minutes=10)  # a daily job this late is skipped, not run
    tick_s: float = 1.0  # longest sleep before looking at the clock again
    _daily: list[_Daily] = field(default_factory=list)
    _every: list[_Every] = field(default_factory=list)
    ran: list[tuple[str, datetime]] = field(default_factory=list)  # (job, when): for /ops
    _running: dict[str, asyncio.Task] = field(default_factory=dict)  # job name -> its run

    def daily(self, at: str, job: Job, name: str | None = None) -> None:
        self._daily.append(_Daily(parse_hhmm(at), job, name or _name(job)))

    def every(
        self,
        seconds: float,
        job: Job,
        *,
        between: tuple[str, str],
        name: str | None = None,
        any_day: bool = False,
    ) -> None:
        start, end = (parse_hhmm(t) for t in between)
        self._every.append(_Every(seconds, job, name or _name(job), start, end, any_day=any_day))

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    async def run(self) -> None:
        while True:
            await self.run_due()
            await self.sleep(self.tick_s)

    async def run_due(self) -> None:
        """Run every job that is due now (called once a second by `run`)."""
        now = self.clock().astimezone(IST)
        trading = self.is_trading_day(now.date())
        started: list[asyncio.Task] = []
        for d in self._daily if trading else ():
            due = datetime.combine(now.date(), d.at, tzinfo=IST)
            if d.last_day == now.date() or now < due:
                continue
            d.last_day = now.date()
            if now - due > self.grace:
                log.warning("skipped %s: due %s, now %s", d.name, d.at, now.time())
                continue
            started += self._start(d.name, d.job, now)
        for e in self._every:
            if not (trading or e.any_day) or not (e.start <= now.time() < e.end):
                e.next_at = None
                continue
            if e.next_at is None or now >= e.next_at:
                e.next_at = now + timedelta(seconds=e.seconds)
                started += self._start(e.name, e.job, now)
        if started:
            await asyncio.wait(started, timeout=WAIT_S)

    def _start(self, name: str, job: Job, now: datetime) -> list[asyncio.Task]:
        """The job's run as a task of its own; none if its last run hasn't finished."""
        busy = self._running.get(name)
        if busy is not None and not busy.done():
            log.warning("job %s is still running from before: skipped this time", name)
            return []
        task = asyncio.ensure_future(self._call(name, job, now))
        self._running[name] = task
        return [task]

    async def _call(self, name: str, job: Job, now: datetime) -> None:
        self.ran.append((name, now))
        del self.ran[:-200]
        try:
            result = job()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("job %s failed", name)  # one failing job never stops the others


def _name(job: Job) -> str:
    return getattr(job, "__qualname__", repr(job))


def parse_holidays(text: str) -> frozenset[date]:
    """'2026-10-21, 2026-11-10' -> dates (NSE's list changes every year: keep it in .env)."""
    return frozenset(date.fromisoformat(d.strip()) for d in text.split(",") if d.strip())


def trading_days(days: Iterable[date], holidays: frozenset[date]) -> list[date]:
    return [d for d in days if d.weekday() < 5 and d not in holidays]
