"""Backups: consistent copies of live databases, kept for N days."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog
from mocktrade.tools.backup import main, run_backup

NOW = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)


def rows(path, table):
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


async def test_a_backup_of_live_databases_holds_every_committed_write(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(SecretBox.new_key()))
    users.create_user("ayush", "Ayush", "a-long-password-1", set())
    log = EventLog(data / "events.db")
    log.start()
    try:
        for n in range(50):
            log.record_note("TEST", "acc-1", f"note {n}", {})
        await log.flush()  # committed, and still sitting in the WAL file (server running)
        assert (data / "events.db-wal").exists()
        folder = run_backup(data, tmp_path / "backups", 14, NOW)
    finally:
        await log.aclose()
    assert folder.name == "20261014T180000Z"
    assert sorted(p.name for p in folder.iterdir()) == ["events.db", "users.db"]
    assert rows(folder / "events.db", "events") == 50
    assert rows(folder / "users.db", "users") == 1


def test_old_backups_are_pruned_and_other_folders_left_alone(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(data / "users.db").execute("CREATE TABLE t (x)").connection.close()
    to = tmp_path / "backups"
    for days in (1, 13, 15, 40):
        (to / (NOW - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")).mkdir(parents=True)
    (to / "20261001T000000Z.part").mkdir()  # left by a run that crashed
    (to / "keep-me").mkdir()
    run_backup(data, to, 14, NOW)
    assert sorted(p.name for p in to.iterdir()) == [
        "20261001T180000Z",
        "20261013T180000Z",
        "20261014T180000Z",
        "keep-me",
    ]


def test_nothing_to_back_up_is_an_error_not_an_empty_backup(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    with pytest.raises(FileNotFoundError):
        run_backup(tmp_path / "data", tmp_path / "b", 14, NOW)
    assert list((tmp_path / "b").iterdir()) == []
    assert main(["--data", str(tmp_path / "data"), "--to", str(tmp_path / "b")]) == 1
    assert "backup FAILED" in capsys.readouterr().err


def test_backups_are_readable_by_their_owner_only(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(data / "users.db").execute("CREATE TABLE t (x)").connection.close()
    assert main(["--data", str(data), "--to", str(tmp_path / "b")]) == 0
    (folder,) = (tmp_path / "b").iterdir()
    assert (tmp_path / "b").stat().st_mode & 0o077 == 0
    assert folder.stat().st_mode & 0o077 == 0
    assert (folder / "users.db").stat().st_mode & 0o077 == 0
