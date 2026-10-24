"""One status for the whole server (#287): each part's state, why, and since when.

    the supervisor's loops ─┐
    each account's recovery ├─► parts() ─► GET /api/status   (admin and risk: the status page)
    the audit log           │          └─► ready()  ─► GET /api/ready  (Docker, the monitor)
    the ops alerts         ─┘                         └─► the metric `ready` (the dashboard)

A part is in one of four states:

    ready     working
    starting  not working yet, and that is expected (recovery after a restart, a loop starting)
    degraded  working with a problem someone should look at (an alert)
    down      not doing its job: new orders are refused, or nothing to trade on

The server is ready when no part is starting or down. Degraded parts don't make it unready:
a missing Kite login must never stop the screens a trader needs to log in (Caddy keeps
checking liveness, GET /api/health, for the same reason).

Down, not just degraded: a critical loop that is restarting or stopped, the supervisor's
watchdog not ticking, a failed recovery, the audit log unable to write, and two ERROR
alerts: the server's own prices silent in market hours, the data disk almost full. Every
other alert (a firm's own price feed, a Kite login, a lost stop) is a degraded part.

Nothing here is hard-coded on the screen: it shows these parts as the server says them.
"""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from mocktrade.ops.alerts import Alert, Alerts
from mocktrade.ops.supervisor import Supervisor

STATES = ("ready", "starting", "degraded", "down")  # better to worse
NOT_READY = ("starting", "down")
LOOK_EVERY_S = 5.0  # watch(): how close "since" is for parts that keep no time of their own
DOWN_WHEN_ERROR = ("prices", "disk")  # these alert keys, at ERROR, are down (see above)
OWN_ROWS = ("audit_log", "loop", "recovery")  # alerts already shown by their part's own row
NAMES = {"prices": "prices", "broker_login": "broker login", "disk": "disk",
         "price_overload": "price load"}  # fmt: skip


@dataclass(frozen=True)
class Part:
    name: str
    state: str  # one of STATES
    reason: str
    since: datetime | None
    account_id: str | None = None  # None: the whole server; else one account's

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "reason": self.reason,
                "since": self.since.isoformat() if self.since else None,
                "account_id": self.account_id}  # fmt: skip


class _Recovering(Protocol):
    state: str
    reason: str
    since: datetime


class _Account(Protocol):
    recovery: _Recovering | None


def worst(parts: Iterable[Part]) -> str:
    return max((p.state for p in parts), key=STATES.index, default="ready")


class Status:
    def __init__(
        self,
        supervisor: Supervisor,
        accounts: Callable[[], Mapping[str, _Account]],
        alerts: Alerts | None,
        audit_problem: Callable[[], str | None],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._supervisor = supervisor
        self._accounts = accounts
        self._alerts = alerts
        self._audit_problem = audit_problem
        self._clock = clock
        self.started = clock()
        self._seen: dict[str, tuple[str, datetime]] = {}  # for parts that keep no time

    def parts(self) -> list[Part]:
        parts = [Part("server", "ready", "answering", self.started)]
        _, problems = self._supervisor.ready()
        stuck = any("watchdog" in p for p in problems)
        parts.append(self._timed("supervisor", "down" if stuck else "ready",
                                 "its watchdog isn't ticking: loops aren't watched" if stuck
                                 else "watching the background loops"))  # fmt: skip
        for loop in sorted(self._supervisor.loops.values(), key=lambda x: x.name):
            parts.append(Part(f"loop {loop.name}", _loop_state(loop.state, loop.critical),
                              _loop_reason(loop.state, loop.last_error, loop.critical),
                              loop.since, loop.account_id))  # fmt: skip
        for account_id, runtime in sorted(self._accounts().items()):
            gate = runtime.recovery
            if gate is not None:
                state = {"ok": "ready", "recovering": "starting"}.get(gate.state, "down")
                reason = "orders and positions rebuilt" if gate.state == "ok" else gate.reason
                parts.append(Part(f"recovery {account_id}", state, reason, gate.since,
                                  account_id))  # fmt: skip
        problem = self._audit_problem()
        parts.append(self._timed("audit log", "down" if problem else "ready",
                                 f"{problem}: new orders are refused" if problem
                                 else "writing"))  # fmt: skip
        parts.extend(self._from_alerts())
        return parts

    async def watch(self, every_s: float = LOOK_EVERY_S) -> None:
        """Look at every part every few seconds, so a part that keeps no time of its own
        (the audit log, the watchdog, "no problem reported") says since when it is in its
        state to within `every_s`, whether or not anyone is looking."""
        while True:
            self.parts()
            await asyncio.sleep(every_s)

    def ready(self) -> tuple[bool, list[Part]]:
        """(ready, the parts that keep it from being ready)."""
        blocking = [p for p in self.parts() if p.state in NOT_READY]
        return (not blocking, blocking)

    def _from_alerts(self) -> list[Part]:
        active = self._alerts.active() if self._alerts is not None else []
        rows: list[Part] = []
        for family in NAMES:  # always shown: "no alert" is news too
            if not any(_family(a) == family for a in active):
                rows.append(self._timed(NAMES[family], "ready", "no problem reported"))
        for alert in active:
            family = _family(alert)
            if family in OWN_ROWS:
                continue
            down = family in DOWN_WHEN_ERROR and alert.level == "ERROR" and alert.account_id is None
            name = NAMES.get(family, family)
            if alert.account_id is not None:
                name = f"{name} {alert.account_id}"
            rows.append(Part(name, "down" if down else "degraded", alert.text, alert.at,
                             alert.account_id))  # fmt: skip
        return rows

    def _timed(self, name: str, state: str, reason: str) -> Part:
        """A part that keeps no time of its own: since when this server first saw it so
        (watch() looks often; the first look at a server's start is its start)."""
        seen = self._seen.get(name)
        if seen is None or seen[0] != state:
            seen = self._seen[name] = (state, self._clock() if seen else self.started)
        return Part(name, state, reason, seen[1])


def _family(alert: Alert) -> str:
    return alert.key.split(":", 1)[0]


def _loop_state(state: str, critical: bool) -> str:
    if state in ("running", "done"):
        return "ready"
    if state == "starting":
        return "starting"
    return "down" if critical else "degraded"  # restarting or quarantined


def _loop_reason(state: str, error: str, critical: bool) -> str:
    if state == "running":
        return "running"
    if state == "done":
        return "finished its work"
    if state == "starting":
        return "starting"
    how = "restarting after a failure"
    if state == "quarantined":
        how = "stopped after repeated failures: restart the server once the cause is fixed"
    return f"{how} ({error}){'; new orders paused' if critical else ''}"
