"""Alerts kept across restarts: one JSON file per user (their alerts and their webhook).

    <root>/<user id>.json   {"alerts": [...], "webhook": {"url": ..., "secret": ...} | null}

Written whole to a temporary file and moved into place (a crash never leaves half a file).
Changes made on the trading loop (an alert armed or fired) are marked and written by `run()`
once a second in a worker thread, so the loop never waits for the disk.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from mocktrade.alerts.engine import Alert, alert_from_dict, alert_to_dict

log = logging.getLogger(__name__)
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class AlertStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._dirty: set[str] = set()
        self._view: dict[str, dict[str, Any]] = {}  # what each user's file holds

    def _path(self, user_id: str) -> Path:
        if not _SAFE.match(user_id):
            raise ValueError(f"not a user id: {user_id!r}")
        return self.root / f"{user_id}.json"

    def load_all(self) -> list[Alert]:
        out: list[Alert] = []
        if not self.root.is_dir():
            return out
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                self._view[path.stem] = data
                out.extend(alert_from_dict(d) for d in data.get("alerts", []))
            except (OSError, ValueError, TypeError) as exc:
                log.warning("alerts %s can't be read: %s", path, exc)
        return out

    def webhook(self, user_id: str) -> dict[str, str] | None:
        return self._view.get(user_id, {}).get("webhook")

    def set_webhook(self, user_id: str, hook: dict[str, str] | None) -> None:
        self._view.setdefault(user_id, {"alerts": []})["webhook"] = hook
        self.write(user_id)

    def changed(self, alerts: list[Alert], user_id: str) -> None:
        """The user's alerts changed: kept in memory now, written by `run()` soon."""
        self._view.setdefault(user_id, {"webhook": None})["alerts"] = [
            alert_to_dict(a) for a in alerts
        ]
        self._dirty.add(user_id)

    def write(self, user_id: str) -> None:
        self._dirty.discard(user_id)
        data = self._view.get(user_id, {"alerts": [], "webhook": None})
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(user_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, path)

    async def flush(self) -> None:
        for user_id in list(self._dirty):
            try:
                await asyncio.to_thread(self.write, user_id)
            except OSError:
                log.exception("alerts of %s not written", user_id)
                self._dirty.add(user_id)  # tried again next time

    async def run(self, every_s: float = 1.0) -> None:
        try:
            while True:
                await asyncio.sleep(every_s)
                await self.flush()
        finally:
            await asyncio.shield(self.flush())
