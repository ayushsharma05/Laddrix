"""Each user's spread definitions, kept across restarts (#414).

    <root>/<hash of the user id>.json      {"user": <id>, "spreads": [...]}

The same way alerts are kept (alerts/store.py): a file per user, named by a hash so any id is
a safe name, written whole to a temporary file and moved into place (never half a file).
Only the owner (the same user, in the same workspace) sees or changes a spread.
A change is made in memory at once (the screens and the pricer see it) and written to disk
in a worker thread, so the trading loop never waits for the disk.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from laddrix.domain.accounts import User
from laddrix.domain.instruments import Instrument
from laddrix.spreads.model import (
    MAX_SPREADS_PER_USER,
    PREFIX,
    Controls,
    SpreadDef,
    SpreadLeg,
    check,
    spread_from_dict,
    spread_to_dict,
)

log = logging.getLogger(__name__)


class SpreadError(Exception):
    def __init__(self, problems: list[str], *, missing: bool = False) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
        self.missing = missing  # no such spread (or not this user's): 404, not 422


Listener = Callable[[str, SpreadDef | None], None]  # (spread id, its new definition or None)


class SpreadBook:
    def __init__(
        self,
        root: Path | None,
        lookup: Callable[[str], Instrument | None],
        expired: Callable[[Instrument], bool] = lambda _i: False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = Path(root) if root is not None else None  # None: kept in memory only
        self._lookup = lookup
        self._expired = expired
        self._clock = clock
        self._by_id: dict[str, SpreadDef] = {}
        self._listeners: list[Listener] = []

    @classmethod
    def at(cls, root: Path, lookup: Callable[[str], Instrument | None],
           expired: Callable[[Instrument], bool] = lambda _i: False) -> "SpreadBook":  # fmt: skip
        """The book kept in `root`, loaded as it was before the restart (the servers' start)."""
        book = cls(root, lookup, expired)
        book.load()
        return book

    # --- reading ---------------------------------------------------------------------------

    def load(self) -> None:
        if self.root is None or not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                for d in data.get("spreads", []):
                    spread = spread_from_dict(d)
                    self._by_id[spread.spread_id] = spread
            except (OSError, ValueError, TypeError, KeyError) as exc:
                log.warning("spreads %s can't be read: %s", path, exc)

    def get(self, spread_id: str) -> SpreadDef | None:
        return self._by_id.get(spread_id)

    def of_instrument(self, instrument_id: str) -> SpreadDef | None:
        """The spread an id like "SPREAD:<id>" names (anyone's: for the pricer)."""
        if not instrument_id.startswith(PREFIX):
            return None
        return self._by_id.get(instrument_id[len(PREFIX) :])

    def visible(self, user: User, instrument_id: str) -> SpreadDef | None:
        """The spread, if this user may see it (only its owner)."""
        found = self.of_instrument(instrument_id)
        return found if found is not None and _owns(user, found) else None

    def mine(self, user: User) -> list[SpreadDef]:
        return sorted((s for s in self._by_id.values() if _owns(user, s)),
                      key=lambda s: (s.name.lower(), s.spread_id))  # fmt: skip

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- changing (each one is written by `save`) ------------------------------------------

    def create(
        self, user: User, name: str, legs: list[SpreadLeg], controls: Controls | None = None
    ) -> SpreadDef:
        problems = check(name, legs, self._lookup, self._expired)
        if len(self.mine(user)) >= MAX_SPREADS_PER_USER:
            problems.append(f"at most {MAX_SPREADS_PER_USER} spreads: delete one first")
        if problems:
            raise SpreadError(problems)
        now = self._clock()
        spread_id = secrets.token_hex(4)
        while spread_id in self._by_id:
            spread_id = secrets.token_hex(4)
        spread = SpreadDef(spread_id, user.id, user.workspace, name.strip(), tuple(legs), now, now,
                           controls or Controls())  # fmt: skip
        self._by_id[spread_id] = spread
        self._tell(spread_id, spread)
        return spread

    def update(
        self, user: User, spread_id: str, name: str, legs: list[SpreadLeg],
        controls: Controls | None = None,  # None: keep its own
    ) -> SpreadDef:  # fmt: skip
        old = self._by_id.get(spread_id)
        if old is None or not _owns(user, old):
            raise SpreadError([f"no spread {spread_id}"], missing=True)
        problems = check(name, legs, self._lookup, self._expired)
        if problems:
            raise SpreadError(problems)
        spread = SpreadDef(spread_id, old.user_id, old.workspace, name.strip(), tuple(legs),
                           old.created_at, self._clock(), controls or old.controls)  # fmt: skip
        self._by_id[spread_id] = spread
        self._tell(spread_id, spread)
        return spread

    def delete(self, user: User, spread_id: str) -> None:
        old = self._by_id.get(spread_id)
        if old is None or not _owns(user, old):
            raise SpreadError([f"no spread {spread_id}"], missing=True)
        del self._by_id[spread_id]
        self._tell(spread_id, None)

    def _tell(self, spread_id: str, spread: SpreadDef | None) -> None:
        for listener in list(self._listeners):
            try:
                listener(spread_id, spread)
            except Exception:  # one listener's bug must not stop the others
                log.exception("spread listener failed for %s", spread_id)

    # --- the disk --------------------------------------------------------------------------

    def path_of(self, user_id: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / f"{hashlib.sha256(user_id.encode()).hexdigest()[:24]}.json"

    def write(self, user_id: str) -> None:
        path = self.path_of(user_id)
        if path is None:
            return
        spreads = [spread_to_dict(s) for s in self._by_id.values() if s.user_id == user_id]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"user": user_id, "spreads": spreads}, indent=1))
        os.replace(tmp, path)

    async def save(self, user_id: str) -> None:
        """Write one user's spreads, off the trading loop."""
        await asyncio.to_thread(self.write, user_id)


def _owns(user: User, spread: SpreadDef) -> bool:
    return spread.user_id == user.id and spread.workspace == user.workspace
