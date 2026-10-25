"""Restore a backup into a data folder (V1.3). With the server stopped:

    python -m laddrix.tools.restore --from /data/backups/20261016T161500Z --to /data
    python -m laddrix.tools.restore --from … --to /data --replace   # over existing data

    backup folder ─► check: manifest.json present, every file there, sizes and SHA-256 match,
                     each database passes SQLite's integrity check; anything wrong ─► nothing
                     is touched, and the problem is named
                  ─► the data folder: empty (a fresh server; its backups/ may stay), or
                     --replace: what is there now is moved aside to
                     <data>/.before-restore-<time>/ first (never deleted)
                  ─► copy everything into the empty folder: no stale -wal/-shm file can be
                     left to be replayed onto the restored databases
                  ─► say what does not come from a backup and must be put back by hand

Restore into an isolated, fresh folder first to check a backup without touching anything
(context/scripts/drill-backup-restore.py does exactly that).
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from laddrix.tools.backup import DATABASES, FORMAT, MANIFEST, STAMP, sha256

FROM_ELSEWHERE = (
    "LADDRIX_SECRET_KEY (from the password manager): without the same key, users must set "
    "up their authenticator again",
    "deploy/.env and the broker's API secret (from the password manager)",
    "the broker login: tokens are never backed up; log in to Kite again (they expire daily)",
)


class RestoreError(Exception):
    """The backup can't be restored as it is; nothing was changed."""


def check_backup(folder: Path) -> dict:
    """The manifest, once every file in it is present and unchanged."""
    try:
        listed = json.loads((folder / MANIFEST).read_text())
    except FileNotFoundError:
        raise RestoreError(f"{folder}: no {MANIFEST}: not a complete backup") from None
    except ValueError as exc:
        raise RestoreError(f"{folder}: {MANIFEST} is broken: {exc}") from None
    files = listed.get("files")
    if not isinstance(files, dict) or not any(name in files for name in DATABASES):
        raise RestoreError(f"{folder}: the manifest lists no database")
    if listed.get("format") != FORMAT:  # a backup this version can't read: never a guess
        raise RestoreError(f"{folder}: backup format {listed.get('format')!r}, this Laddrix "
                           f"restores format {FORMAT}: use the version that made it")  # fmt: skip
    for name, expect in files.items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise RestoreError(f"{folder}: the manifest names a file outside the backup: {name}")
        path = folder / name
        if not path.is_file():
            raise RestoreError(f"{folder}: {name} is missing")
        if path.stat().st_size != expect["bytes"] or sha256(path) != expect["sha256"]:
            raise RestoreError(f"{folder}: {name} has changed since the backup was made")
    for name in DATABASES:
        if name in files:
            con = sqlite3.connect(f"file:{folder / name}?mode=ro", uri=True)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.DatabaseError as exc:
                result = str(exc)  # too damaged to even check
            finally:
                con.close()
            if result != "ok":
                raise RestoreError(f"{folder}: {name} fails SQLite's integrity check: {result}")
    return listed


LEFT_IN_PLACE = ("backups",)  # the backups themselves (the source may be one of them)
ASIDE = ".before-restore-"


def restore(source: Path, data_dir: Path, *, replace: bool, now: datetime) -> Path | None:
    """Restore `source` into `data_dir`. Returns where the old data was moved, if any.

    The old data is moved into `<data>/.before-restore-<time>/`, inside the same folder: the
    data folder is often a volume's mount point, which can't itself be renamed, and the
    backups (left in place) may be inside it too."""
    listed = check_backup(source)
    present = []
    if data_dir.exists():
        present = [p for p in data_dir.iterdir()
                   if p.name not in LEFT_IN_PLACE and not p.name.startswith(ASIDE)]  # fmt: skip
    moved = None
    if present:
        if not replace:
            raise RestoreError(
                f"{data_dir} is not empty: restore into an empty folder, or pass --replace "
                "(what is there now is moved aside first, not deleted)"
            )
        moved = data_dir / f"{ASIDE}{now.strftime(STAMP)}"
        moved.mkdir(mode=0o700)
        for path in present:
            path.rename(moved / path.name)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in listed["files"]:
        target = data_dir / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    return moved


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="laddrix.tools.restore")
    parser.add_argument("--from", dest="source", type=Path, required=True, help="a backup folder")
    parser.add_argument("--to", type=Path, required=True, help="the data folder (server stopped)")
    parser.add_argument("--replace", action="store_true",
                        help="the data folder has data: move it aside, then restore")  # fmt: skip
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        moved = restore(args.source, args.to, replace=args.replace, now=datetime.now(UTC))
    except (RestoreError, OSError) as exc:
        print(f"restore REFUSED, nothing changed: {exc}", file=sys.stderr)
        return 1
    print(f"restore OK: {args.source} -> {args.to}")
    if moved is not None:
        print(f"the data that was there is kept in {moved}")
    print("Not in any backup; put back by hand before starting:")
    for item in FROM_ELSEWHERE:
        print(f"  - {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
