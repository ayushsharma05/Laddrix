"""Operational alerts: one place, each told once, to every place that should hear it.

    alerts.raise_("kite_login:acc-ayush", "WARN", "Log in to Kite: market opens at 09:15")
    alerts.clear("kite_login:acc-ayush", "Kite login done")

An alert has a key. Raising it again with the same level and text does nothing (no alarm
storms from a check that runs every 30 s); a new level or text tells everyone again.
Clearing an active alert tells everyone it's over. Listeners: the screens (a band and
Messages), the audit trail, and optionally a webhook to a phone (ops/notify.py).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)

LEVELS = ("OK", "INFO", "WARN", "ERROR")


@dataclass(frozen=True)
class Alert:
    key: str
    level: str  # INFO, WARN, ERROR; OK when cleared
    text: str
    active: bool
    at: datetime


Listener = Callable[[Alert], None]


class Alerts:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._active: dict[str, Alert] = {}
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def active(self) -> list[Alert]:
        return sorted(self._active.values(), key=lambda a: (-LEVELS.index(a.level), a.key))

    def raise_(self, key: str, level: str, text: str) -> None:
        if level not in LEVELS[1:]:
            raise ValueError(f"level must be INFO, WARN or ERROR, got {level!r}")
        old = self._active.get(key)
        if old is not None and (old.level, old.text) == (level, text):
            return
        alert = Alert(key, level, text, True, self._clock())
        self._active[key] = alert
        self._tell(alert)

    def clear(self, key: str, text: str = "") -> None:
        old = self._active.pop(key, None)
        if old is not None:
            self._tell(Alert(key, "OK", text or f"resolved: {old.text}", False, self._clock()))

    def _tell(self, alert: Alert) -> None:
        log.log(
            logging.WARNING if alert.level in ("WARN", "ERROR") else logging.INFO,
            "ops alert %s %s: %s",
            alert.level,
            alert.key,
            alert.text,
        )
        for listener in self._listeners:
            try:
                listener(alert)
            except Exception:
                log.exception("alert listener failed")


def alert_to_dict(a: Alert) -> dict:
    return {
        "type": "ops",
        "key": a.key,
        "level": a.level,
        "text": a.text,
        "active": a.active,
        "at": a.at.isoformat(),
    }
