"""Critical background loops, watched (#273): a living web server isn't a healthy trading one.

    keep(name, loop) ─► runs it; it ends by itself     ─► done (a one-shot, e.g. recovery)
                     ─► it raises                      ─► said (an alert), restarted after a
                                                          pause that grows: 1 s, 2 s, 4 s ... 30 s
                     ─► 5 failures within 5 minutes    ─► quarantined: not restarted, an ERROR
                                                          alert, until the server restarts
    a CRITICAL loop that is down (restarting or quarantined) pauses new orders for its account
    (or all accounts, for a firm-wide loop): PauseGate refuses new orders and changes, cancels
    still go. It reopens by itself once the loop runs again.

    ready() ─► the loops' state, for GET /api/ready (liveness stays GET /api/health)
            ─► the supervisor's own heartbeat: if its watchdog stops ticking, ready() says so

Critical loops: an account's broker updates (fills), its recovery after a restart, its
position reconciling and GTT watching, the algos' timer, and marking positions to prices.
Others (portfolio reads, scheduled jobs) are watched the same way but pause nothing.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger(__name__)

BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
QUARANTINE_AFTER = 5  # failures ...
QUARANTINE_WINDOW_S = 300.0  # ... within this many seconds
HEARTBEAT_S = 1.0
STALE_AFTER_S = 10.0  # the watchdog hasn't ticked for this long: the supervisor itself is stuck


@dataclass
class LoopState:
    name: str
    critical: bool
    account_id: str | None  # None: firm-wide
    state: str = "starting"  # starting | running | restarting | quarantined | done
    failures: list[float] = field(default_factory=list)  # monotonic times of failures
    last_error: str = ""
    since: datetime | None = None  # when it entered its state (the status page, #287)

    @property
    def down(self) -> bool:
        return self.state in ("restarting", "quarantined")


class Supervisor:
    def __init__(
        self,
        *,
        alert: Callable[[str, str, str, str | None], None] = lambda _k, _l, _t, _a: None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._alert = alert  # (key, level, text, account_id): ops alerts
        self._now = monotonic
        self._clock = clock
        self._sleep = sleep
        self.loops: dict[str, LoopState] = {}
        self._beat: float | None = None

    def set_alert(self, alert: Callable[[str, str, str, str | None], None]) -> None:
        """Where loop failures are said (the server's ops alerts)."""
        self._alert = alert

    # --- running loops -----------------------------------------------------------------------

    async def keep(self, name: str, factory: Callable[[], Awaitable[object]], *,
                   critical: bool = True, account_id: str | None = None) -> None:  # fmt: skip
        """Run `factory()` and keep it running (see the module notes). Cancelling this ends it."""
        loop = self.loops[name] = LoopState(name, critical, account_id)

        def enter(state: str) -> None:
            loop.state, loop.since = state, self._clock()

        while True:
            enter("running")
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = self._now()
                loop.failures = [t for t in loop.failures if now - t < QUARANTINE_WINDOW_S]
                loop.failures.append(now)
                loop.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("%s failed", name)
                if len(loop.failures) >= QUARANTINE_AFTER:
                    enter("quarantined")
                    self._alert(f"loop:{name}", "ERROR",
                                f"{name} keeps failing ({loop.last_error}): stopped trying"
                                f"{'; new orders paused' if critical else ''}. Restart the "
                                "server once the cause is fixed", account_id)  # fmt: skip
                    return
                wait = BACKOFF_S[min(len(loop.failures) - 1, len(BACKOFF_S) - 1)]
                enter("restarting")
                self._alert(f"loop:{name}", "ERROR" if critical else "WARN",
                            f"{name} failed ({loop.last_error}): restarting in {wait:g} s"
                            f"{'; new orders paused until it runs' if critical else ''}",
                            account_id)  # fmt: skip
                await self._sleep(wait)
                self._alert(f"loop:{name}", "OK", f"{name} restarted", account_id)
                continue
            enter("done")
            return

    async def watchdog(self) -> None:
        """The supervisor's own heartbeat: ready() reports a stuck supervisor (#273)."""
        while True:
            self._beat = self._now()
            await self._sleep(HEARTBEAT_S)

    # --- what it says ------------------------------------------------------------------------

    def paused(self, account_id: str) -> str | None:
        """Why new orders on this account must wait, or None."""
        for loop in self.loops.values():
            if loop.critical and loop.down and loop.account_id in (None, account_id):
                how = "stopped after repeated failures" if loop.state == "quarantined" else (
                    "restarting")  # fmt: skip
                return f"{loop.name} is {how} ({loop.last_error}): new orders wait; cancels go"
        return None

    def ready(self) -> tuple[bool, list[str]]:
        """(ready, the reasons it isn't)."""
        problems = [f"{loop.name}: {loop.state} ({loop.last_error})"
                    for loop in self.loops.values() if loop.critical and loop.down]  # fmt: skip
        if self._beat is None or self._now() - self._beat > STALE_AFTER_S:
            problems.append("the supervisor's watchdog isn't ticking")
        return (not problems, problems)


class PauseGate:
    """An order gate: new orders and changes wait while a critical loop is down."""

    def __init__(self, supervisor: Supervisor, account_id: str) -> None:
        self._supervisor = supervisor
        self._account_id = account_id

    async def admit_new(self, request) -> str | None:
        return self._supervisor.paused(self._account_id)

    async def admit_modify(self, order, new) -> str | None:
        return self._supervisor.paused(self._account_id)

    async def admit_cancel(self, order) -> str | None:
        return None  # cancelling only ever reduces risk
