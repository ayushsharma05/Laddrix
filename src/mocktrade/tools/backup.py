"""Back up MockTrade's databases safely while the server runs.

    python -m mocktrade.tools.backup --to /data/backups            # keep 14 days
    python -m mocktrade.tools.backup --to /data/backups --keep 30

Copying a live SQLite file can give a torn copy (the newest writes sit in the -wal file),
so each database is copied with SQLite's online backup, which is consistent even while
the server writes. Each run makes one folder named by the time (UTC), checks every copy
opens and passes SQLite's integrity check, then deletes folders older than `--keep` days.

What is backed up: users.db (users, accounts, sessions), events.db (the audit trail) and
saved layouts within them. Broker tokens are not: they expire at 6 AM anyway.
Copy the backup folder off the machine too (see docs/runbooks/server.md).
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DATABASES = ("users.db", "events.db")
STAMP = "%Y%m%dT%H%M%SZ"


def backup_one(source: Path, target: Path) -> None:
    """A consistent copy of a live database, verified."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal/-shm
    finally:
        src.close()
        dst.close()
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"backup of {source.name} failed its integrity check: {result}")


def run_backup(data_dir: Path, to: Path, keep_days: int, now: datetime) -> Path:
    """Back up every database in `data_dir` into a new folder under `to`; prune old ones."""
    folder = to / now.strftime(STAMP)
    part = to / (folder.name + ".part")  # renamed only when complete: never a half backup
    shutil.rmtree(part, ignore_errors=True)
    to.mkdir(mode=0o700, parents=True, exist_ok=True)
    part.mkdir(mode=0o700)
    copied = 0
    for name in DATABASES:
        source = data_dir / name
        if source.exists():
            backup_one(source, part / name)
            copied += 1
    if not copied:
        shutil.rmtree(part)
        raise FileNotFoundError(f"no databases in {data_dir}")
    part.rename(folder)
    cutoff = now - timedelta(days=keep_days)
    for old in to.iterdir():
        if old == folder or not old.is_dir():
            continue
        try:
            made = datetime.strptime(old.name.removesuffix(".part"), STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue  # not ours: leave it alone
        if made < cutoff or old.name.endswith(".part"):
            shutil.rmtree(old)
    return folder


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mocktrade.tools.backup")
    parser.add_argument("--data", type=Path, default=None, help="default: $MOCKTRADE_DATA or data")
    parser.add_argument("--to", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=14, help="days of backups to keep")
    args = parser.parse_args(argv)
    os.umask(0o077)  # backups hold password hashes: owner only
    data = args.data or Path(os.environ.get("MOCKTRADE_DATA", "data"))
    try:
        folder = run_backup(data, args.to, args.keep, datetime.now(UTC))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"backup FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"backup OK: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
