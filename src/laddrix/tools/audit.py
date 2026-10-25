"""Check that the audit trail is whole (#367).

    python -m laddrix.tools.audit verify                    # $LADDRIX_DATA/events.db
    python -m laddrix.tools.audit verify --data /data --against /backups/<stamp>/manifest.json

    events.db ─► recompute every row's chain (store/events.py) ─► the first row that doesn't
                 follow: changed, or a row before it changed or removed ─► exit 1
              ─► --against a backup's manifest: the head it recorded must still be in the log,
                 with the same chain (a whole rewritten log is caught here) ─► else exit 1

Read-only: safe while the server runs. Rows written before #367 have no chain; they are
counted, not checked.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from laddrix.store.events import verify_chain


def against(events: Path, manifest: Path) -> str | None:
    """None if the head a backup recorded is still in the log, unchanged; else why not."""
    recorded = json.loads(manifest.read_text()).get("audit_chain")
    if not recorded or recorded.get("head") is None:
        return f"{manifest} records no audit chain head"
    db = sqlite3.connect(f"file:{events}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT chain FROM events WHERE seq = ?", (recorded["head_seq"],))
        found = row.fetchone()
    finally:
        db.close()
    if found is None:
        return f"row {recorded['head_seq']}, the head recorded by {manifest}, is gone"
    if found[0] != recorded["head"]:
        return (f"row {recorded['head_seq']} has a different chain than {manifest} recorded: "
                "the log was rewritten up to there")  # fmt: skip
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="laddrix.tools.audit")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--data", type=Path, default=None, help="default: $LADDRIX_DATA or data")
    verify.add_argument("--against", type=Path, help="a backup's manifest.json")
    args = parser.parse_args(argv)
    events = (args.data or Path(os.environ.get("LADDRIX_DATA", "data"))) / "events.db"
    if not events.exists():
        print(f"no audit trail at {events}", file=sys.stderr)
        return 1
    report = verify_chain(events)
    print(f"{report.rows} rows; {report.unchained} from before chaining (not checked)")
    if not report.ok:
        print(f"BROKEN at row {report.broken_at}: {report.why}", file=sys.stderr)
        return 1
    print(f"whole: every chained row follows; head row {report.head_seq} {report.head}")
    if args.against is not None:
        if problem := against(events, args.against):
            print(f"BROKEN: {problem}", file=sys.stderr)
            return 1
        print(f"matches the head recorded in {args.against}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
