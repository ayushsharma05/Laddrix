"""The audit trail is tamper-evident (#367).

every row: chain = SHA-256(previous chain + the row)
a row changed or removed ─► every later chain stops following ─► verify names the first
the whole log rewritten (chains recomputed) ─► only a head kept elsewhere catches it: each
                                              backup's manifest records the head
"""

import json
import sqlite3
from datetime import UTC, datetime

from laddrix.store.events import EventLog, link, verify_chain
from laddrix.tools import audit
from laddrix.tools.backup import run_backup


async def written(path, notes, start=0):
    log = EventLog(path)
    log.start()
    for n in range(start, start + notes):
        log.record_note("NOTE", "acc", f"note {n}", {"n": n})
    await log.flush()
    await log.aclose()


def run(db_path, sql, *args):
    with sqlite3.connect(db_path) as db:
        db.execute(sql, args)


def rechain(db_path):
    """What someone with the file and this code could do: recompute every chain."""
    with sqlite3.connect(db_path) as db:
        previous = ""
        rows = db.execute("SELECT seq, ts, account_id, kind, client_order_id, detail, payload "
                          "FROM events ORDER BY seq").fetchall()  # fmt: skip
        for seq, *row in rows:
            previous = link(previous, tuple(row))
            db.execute("UPDATE events SET chain = ? WHERE seq = ?", (previous, seq))


async def test_a_whole_log_verifies_and_reopening_continues_the_chain(tmp_path):
    path = tmp_path / "events.db"
    await written(path, 3)
    await written(path, 2, start=3)  # a restart: the head is read back from the file
    report = verify_chain(path)
    assert report.ok and (report.rows, report.unchained, report.head_seq) == (5, 0, 5)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT chain FROM events WHERE seq = 5").fetchone()[0] == report.head


async def test_a_changed_or_removed_row_is_found(tmp_path):
    path = tmp_path / "events.db"
    await written(path, 5)
    run(path, "UPDATE events SET detail = 'nothing happened' WHERE seq = 2")
    report = verify_chain(path)
    assert (report.ok, report.broken_at) == (False, 2)
    assert "changed" in report.why

    other = tmp_path / "other.db"
    await written(other, 5)
    run(other, "DELETE FROM events WHERE seq = 3")
    assert verify_chain(other).broken_at == 4  # the row after the gap no longer follows


async def test_a_rewritten_or_cut_log_is_caught_against_a_backups_head(tmp_path, capsys):
    data, backups = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    await written(data / "events.db", 4)
    (data / "users.db").touch()
    folder = run_backup(data, backups, 14, datetime(2026, 10, 23, 16, tzinfo=UTC))
    recorded = json.loads((folder / "manifest.json").read_text())["audit_chain"]
    assert (recorded["rows"], recorded["head_seq"], recorded["broken_at"]) == (4, 4, None)
    manifest = str(folder / "manifest.json")
    assert audit.main(["verify", "--data", str(data), "--against", manifest]) == 0
    assert "matches the head" in capsys.readouterr().out

    await written(data / "events.db", 2, start=4)  # the log goes on: still matches
    assert audit.main(["verify", "--data", str(data), "--against", manifest]) == 0

    run(data / "events.db", "UPDATE events SET detail = 'quietly edited' WHERE seq = 1")
    rechain(data / "events.db")  # chains recomputed: the log alone looks whole
    assert verify_chain(data / "events.db").ok
    assert audit.main(["verify", "--data", str(data), "--against", manifest]) == 1
    assert "rewritten" in capsys.readouterr().err

    cut = tmp_path / "cut"
    cut.mkdir()
    await written(cut / "events.db", 4)
    run(cut / "events.db", "DELETE FROM events WHERE seq >= 3")  # the newest rows removed
    assert verify_chain(cut / "events.db").ok  # alone, a cut end can't be seen…
    capsys.readouterr()
    assert audit.main(["verify", "--data", str(cut), "--against", manifest]) == 1
    assert "is gone" in capsys.readouterr().err  # …against a head kept elsewhere, it can


async def test_a_log_from_before_chaining_is_counted_not_checked(tmp_path, capsys):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as db:  # the schema before #367: no chain column
        db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                   "account_id TEXT NOT NULL, kind TEXT NOT NULL, client_order_id TEXT NOT NULL, "
                   "detail TEXT NOT NULL, payload TEXT NOT NULL)")  # fmt: skip
        for n in range(3):
            db.execute("INSERT INTO events (ts, account_id, kind, client_order_id, detail, "
                       "payload) VALUES ('2026-10-01', 'acc', 'NOTE', '', ?, '{}')",
                       (f"old {n}",))  # fmt: skip
    await written(path, 2)
    report = verify_chain(path)
    assert report.ok and (report.rows, report.unchained) == (5, 3)
    assert audit.main(["verify", "--data", str(tmp_path)]) == 0
    assert "3 from before chaining (not checked)" in capsys.readouterr().out

    run(path, "INSERT INTO events (ts, account_id, kind, client_order_id, detail, payload) "
              "VALUES ('2026-10-24', 'acc', 'NOTE', '', 'slipped in', '{}')")  # fmt: skip
    report = verify_chain(path)
    assert (report.broken_at, report.why) == (6, "a row without a chain after chained rows")
    assert audit.main(["verify", "--data", str(tmp_path)]) == 1


def test_the_tool_says_when_there_is_nothing_to_check(tmp_path, capsys):
    assert audit.main(["verify", "--data", str(tmp_path)]) == 1
    assert "no audit trail" in capsys.readouterr().err
