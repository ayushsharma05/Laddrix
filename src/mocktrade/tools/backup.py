"""Back up everything a restored MockTrade server needs, safely while the server runs (V1.3).

    python -m mocktrade.tools.backup --to /data/backups               # keep 14 days
    python -m mocktrade.tools.backup --to /data/backups --keep 30
    python -m mocktrade.tools.backup --to /data/backups --with-ticks  # recordings too (large)

Each run makes one folder named by the time (UTC), written as `<name>.part` and renamed only
when complete, so a half backup never looks like a backup. Then folders older than `--keep`
days are deleted.

What goes in (the data folder, $MOCKTRADE_DATA):

    users.db, events.db  users, accounts, layouts, settings, limits; the audit trail
                         (orders, fills, positions are rebuilt from it). Copied with SQLite's
                         online backup: consistent even while the server writes, and checked
                         with SQLite's integrity check. Logged-in sessions are left out: a
                         restore never brings one back, everyone logs in again (#364).
    rules/  reports/  alerts/  candles/  backtests/
                         the traders' MTM limits, end-of-day reports, market alerts (with each
                         user's webhook), imported candles, backtest results. Plain files, each
                         written whole by the server (a temporary file renamed into place).
    ticks/               the recorded days: only with --with-ticks (they are large, and
                         losing them loses no trading state).

What never goes in: secrets and things that expire. The broker tokens (kite_token_*.json:
they expire at 6 AM anyway), the secret key and `.env` (from the password manager, not a
backup folder), the daily instrument list (downloaded again), temporary files, and earlier
backups. `manifest.json` lists every file with its size and SHA-256, so a restore can prove
the backup is whole (mocktrade.tools.restore). Backups hold password hashes and users'
webhook secrets: they are readable by their owner only. Copy them off the machine too
(docs/runbooks/server.md).
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mocktrade.store.events import verify_chain

DATABASES = ("users.db", "events.db")
FOLDERS = ("rules", "reports", "alerts", "spreads", "candles", "backtests")
OPTIONAL = {"ticks": "recorded days (large): --with-ticks"}
NEVER = ("kite_token_*.json", "*.key", ".env", "*.tmp", "*.part")  # inside the folders, too
# In the data folder but rebuilt, renewed or kept elsewhere: not state a backup must hold.
# Anything else at the top of the data folder that isn't a database or a folder above is
# NEW state no backup rule covers yet (#272): the backup is still made, but it says so and
# fails, so the nightly job's alert reaches a person instead of a backup quietly missing it.
REPRODUCIBLE = (
    "backups", ".before-restore-*",  # the backups themselves; what a restore moved aside
    "ticks",  # recordings: only with --with-ticks
    "kite_instruments.csv", "kite_token_*.json",  # downloaded again; expire daily
    "*.db-wal", "*.db-shm", "*.db-journal",  # SQLite's working files (inside the backups)
    "*.tmp", "*.part", "*.key", ".env",
    "demo_totp.txt",  # the demo's login secret: a secret, kept elsewhere (the demo prints it)
    "server.lock",  # who owns the data folder now (#273): the running server's, not state
)  # fmt: skip
MANIFEST = "manifest.json"
FORMAT = 1
STAMP = "%Y%m%dT%H%M%SZ"


# tables emptied in the copy: sessions (a restore must never bring an old login back, #364)
LEFT_OUT = {"users.db": ("sessions",)}


def backup_one(source: Path, target: Path) -> None:
    """A consistent copy of a live database, verified."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        if empty := LEFT_OUT.get(source.name):
            dst.execute("PRAGMA secure_delete = ON")  # overwritten, not just freed: no trace
            have = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in empty:
                if table in have:
                    dst.execute(f"DELETE FROM {table}")
            dst.commit()
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


def never(path: Path) -> bool:
    """A secret, something that expires, or a half-written file: never in a backup."""
    return any(path.match(pattern) for pattern in NEVER)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_folder(source: Path, target: Path) -> None:
    """Every file of a folder the server writes, minus what never goes in a backup."""
    for path in sorted(source.rglob("*")):
        if path.is_file() and not path.is_symlink() and not never(path):
            dest = target / path.relative_to(source)
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def manifest(folder: Path, now: datetime, parts: list[str]) -> dict[str, Any]:
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():  # (made before the manifest is written)
            name = path.relative_to(folder).as_posix()
            files[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return {"format": FORMAT, "made_at": now.isoformat(), "parts": parts, "files": files}


def not_covered(data_dir: Path) -> list[str]:
    """Top-level entries of the data folder that no backup rule covers (see REPRODUCIBLE)."""
    known = set(DATABASES) | set(FOLDERS)
    return sorted(
        p.name for p in data_dir.iterdir()
        if p.name not in known and not any(p.match(pattern) for pattern in REPRODUCIBLE)
    )  # fmt: skip


def run_backup(
    data_dir: Path, to: Path, keep_days: int, now: datetime, *, with_ticks: bool = False
) -> Path:
    """Back up `data_dir` into a new folder under `to`; prune old ones. Returns the folder."""
    folder = to / now.strftime(STAMP)
    part = to / (folder.name + ".part")  # renamed only when complete: never a half backup
    shutil.rmtree(part, ignore_errors=True)
    to.mkdir(mode=0o700, parents=True, exist_ok=True)
    part.mkdir(mode=0o700)
    parts = []
    try:
        for name in DATABASES:
            source = data_dir / name
            if source.exists():
                backup_one(source, part / name)
                parts.append(name)
        if not parts:
            raise FileNotFoundError(f"no databases in {data_dir}")
        for name in FOLDERS + (("ticks",) if with_ticks else ()):
            if (data_dir / name).is_dir():
                copy_folder(data_dir / name, part / name)
                parts.append(f"{name}/")
        listed = manifest(part, now, parts)
        listed["not_covered"] = not_covered(data_dir)
        if (part / "events.db").exists():  # the audit trail's head, kept off the machine (#367)
            chain = verify_chain(part / "events.db")
            listed["audit_chain"] = {"rows": chain.rows, "unchained": chain.unchained,
                                     "head_seq": chain.head_seq, "head": chain.head,
                                     "broken_at": chain.broken_at}  # fmt: skip
        (part / MANIFEST).write_text(json.dumps(listed, indent=1))
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
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
    parser.add_argument("--with-ticks", action="store_true", help=OPTIONAL["ticks"])
    args = parser.parse_args(argv)
    os.umask(0o077)  # backups hold password hashes and webhook secrets: owner only
    data = args.data or Path(os.environ.get("MOCKTRADE_DATA", "data"))
    try:
        folder = run_backup(data, args.to, args.keep, datetime.now(UTC), with_ticks=args.with_ticks)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"backup FAILED: {exc}", file=sys.stderr)
        return 1
    listed = json.loads((folder / MANIFEST).read_text())
    if listed.get("not_covered"):
        print(f"backup made, but INCOMPLETE: {folder}: {', '.join(listed['not_covered'])} in "
              f"{data} is covered by no backup rule (tools/backup.py: FOLDERS for state, "
              "REPRODUCIBLE for what is rebuilt)", file=sys.stderr)  # fmt: skip
        return 2
    print(f"backup OK: {folder} ({len(listed['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
