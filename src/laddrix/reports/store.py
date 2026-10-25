"""Kept reports: one JSON file per account and day, written after the close.

    <data>/reports/<account>/<YYYY-MM-DD>.json

A file per day is easy to back up, read and diff, and needs no schema. It's written to a
temporary file first and renamed, so a crash never leaves half a report.
"""

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

SAFE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class ReportStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, report: dict[str, Any]) -> Path:
        path = self._path(report["account_id"], date.fromisoformat(report["day"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=1, sort_keys=True))
        os.replace(tmp, path)
        return path

    def load(self, account_id: str, day: date) -> dict[str, Any] | None:
        path = self._path(account_id, day)
        return json.loads(path.read_text()) if path.exists() else None

    def days(self, account_id: str) -> list[date]:
        folder = self._folder(account_id)
        if not folder.is_dir():
            return []
        found = []
        for f in folder.glob("*.json"):
            try:
                found.append(date.fromisoformat(f.stem))
            except ValueError:
                continue
        return sorted(found)

    def between(self, account_id: str, first: date, last: date) -> list[dict[str, Any]]:
        """Kept reports with first <= day <= last, oldest first."""
        reports = []
        for day in self.days(account_id):
            if first <= day <= last and (r := self.load(account_id, day)) is not None:
                reports.append(r)
        return reports

    def _folder(self, account_id: str) -> Path:
        if not SAFE.fullmatch(account_id):
            raise ValueError(f"not a safe account id for a folder: {account_id!r}")
        return self.root / account_id

    def _path(self, account_id: str, day: date) -> Path:
        return self._folder(account_id) / f"{day.isoformat()}.json"
