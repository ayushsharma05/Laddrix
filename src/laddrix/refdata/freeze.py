"""Freeze quantities (#424): the exchange's most per order, for each underlying's futures and
options. An order above it is refused by the exchange, so Laddrix splits bigger quantities.

Kite's instrument list doesn't carry them; the exchange publishes them (NSE: a file on its
website, changed from time to time by circular). The operator keeps a small file of them,
set with LADDRIX_FREEZE_FILE:

    # underlying,freeze_qty      (lines starting with # are notes)
    NIFTY,1800
    BANKNIFTY,900

A line that can't be read is reported in words and skipped; it never stops the server.
"""

import logging
from pathlib import Path

from laddrix.refdata.store import InstrumentStore

log = logging.getLogger(__name__)


def parse_freeze(text: str) -> tuple[dict[str, int], list[str]]:
    """The freeze quantity per underlying, and what was wrong with each bad line."""
    found: dict[str, int] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2 or not parts[0]:
            problems.append(f"line {number}: expected underlying,freeze_qty, got {line!r}")
            continue
        underlying, qty = parts[0].upper(), parts[1]
        if underlying == "UNDERLYING":
            continue  # a header line
        if not qty.isdigit() or int(qty) <= 0:
            problems.append(f"line {number}: {underlying}'s freeze quantity must be a whole "
                            f"number above 0, got {qty!r}")  # fmt: skip
            continue
        if underlying in found:
            problems.append(f"line {number}: {underlying} is listed twice (the first is kept)")
            continue
        found[underlying] = int(qty)
    return found, problems


def load_freeze(path: Path) -> tuple[dict[str, int], list[str]]:
    """The file's freeze quantities; OSError if it can't be read at all."""
    return parse_freeze(path.read_text(encoding="utf-8"))


def apply_freeze(store: InstrumentStore, path: Path) -> list[str]:
    """Load the file into the instrument store (now, and for each daily refresh). Every
    problem is logged as a warning and returned; none stops the server."""
    try:
        found, problems = load_freeze(path)
    except OSError as exc:
        found, problems = {}, [f"can't read it: {exc.strerror or exc}"]
    problems += store.set_freeze(found)
    for problem in problems:
        log.warning("freeze quantities (%s): %s", path, problem)
    log.info("freeze quantities: %d underlyings from %s", len(found), path)
    return problems
