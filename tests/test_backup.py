"""Backups: consistent copies of live databases, kept for N days; and since V1.3 (#329) every
folder a restored server needs, never a secret, with a manifest a restore checks first."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore
from mocktrade.store.events import EventLog
from mocktrade.tools.backup import main, run_backup
from mocktrade.tools.restore import RestoreError, check_backup, restore
from mocktrade.tools.restore import main as restore_main

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
    assert sorted(p.name for p in folder.iterdir()) == ["events.db", "manifest.json", "users.db"]
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


# --- V1.3: everything a restored server needs, never a secret ---------------------------------


def a_server(data):
    """A data folder as a running server leaves it: databases, its folders, secrets, caches."""
    data.mkdir(parents=True, exist_ok=True)
    users = UserStore(data / "users.db", SecretBox(SecretBox.new_key()))
    users.create_user("ayush", "Ayush", "a-long-password-1", set())
    files = {
        "rules/mtm-acc-1.json": '{"position": {}}',
        "reports/acc-1/2026-10-14.json": '{"day": "2026-10-14"}',
        "alerts/ab12.json": '{"user": "ayush", "webhook": {"url": "https://x", "secret": "s"}}',
        "candles/NFO/NIFTY.json": "[]",
        "backtests/BT1/run.json": "{}",
        "ticks/2026-10-14/NFO.ticks.gz": "ticks",
        "kite_token_acc-1.json": '{"access_token": "never"}',  # expires daily; a secret
        "secret.key": "never",
        ".env": "MOCKTRADE_SECRET_KEY=never",
        "rules/mtm-acc-2.tmp": "half written",
        "kite_instruments.csv": "downloaded again each morning",
        "backups/20261013T180000Z/users.db": "an earlier backup",
        "alerts/kite_token_x.json": "a token, even in a folder",
    }
    for name, text in files.items():
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text(text)
    return users


KEPT = ["alerts/ab12.json", "backtests/BT1/run.json", "candles/NFO/NIFTY.json",
        "reports/acc-1/2026-10-14.json", "rules/mtm-acc-1.json", "users.db"]  # fmt: skip


def test_a_backup_has_every_folder_a_server_needs_and_never_a_secret(tmp_path):
    a_server(tmp_path / "data")
    folder = run_backup(tmp_path / "data", tmp_path / "b", 14, NOW)
    found = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert found == sorted([*KEPT, "manifest.json"])
    listed = json.loads((folder / "manifest.json").read_text())
    assert listed["format"] == 1 and listed["made_at"] == NOW.isoformat()
    assert listed["parts"] == ["users.db", "rules/", "reports/", "alerts/", "candles/",
                               "backtests/"]  # fmt: skip
    assert sorted(listed["files"]) == KEPT
    assert listed["files"]["rules/mtm-acc-1.json"]["bytes"] == len('{"position": {}}')
    assert all(len(f["sha256"]) == 64 for f in listed["files"].values())


def test_recordings_only_when_asked(tmp_path, capsys):
    a_server(tmp_path / "data")
    folder = run_backup(tmp_path / "data", tmp_path / "b", 14, NOW, with_ticks=True)
    assert (folder / "ticks/2026-10-14/NFO.ticks.gz").read_text() == "ticks"
    assert "ticks/" in json.loads((folder / "manifest.json").read_text())["parts"]
    assert main(["--data", str(tmp_path / "data"), "--to", str(tmp_path / "c")]) == 0
    assert "backup OK" in capsys.readouterr().out
    (made,) = (tmp_path / "c").iterdir()
    assert not (made / "ticks").exists()
    assert main(["--data", str(tmp_path / "data"), "--to", str(tmp_path / "d"),
                 "--with-ticks"]) == 0  # fmt: skip
    (made,) = (tmp_path / "d").iterdir()
    assert (made / "ticks").is_dir()


def test_a_failed_backup_leaves_nothing_half_made(tmp_path, monkeypatch):
    a_server(tmp_path / "data")
    import mocktrade.tools.backup as backup

    def broken(*_):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "copy_folder", broken)
    with pytest.raises(OSError, match="disk full"):
        run_backup(tmp_path / "data", tmp_path / "b", 14, NOW)
    assert list((tmp_path / "b").iterdir()) == []


# --- restore ------------------------------------------------------------------------------------


def backed_up(tmp_path):
    a_server(tmp_path / "data")
    return run_backup(tmp_path / "data", tmp_path / "b", 14, NOW)


def test_a_restore_into_a_fresh_folder_gives_back_every_file(tmp_path):
    folder = backed_up(tmp_path)
    assert restore(folder, tmp_path / "fresh", replace=False, now=NOW) is None
    found = sorted(p.relative_to(tmp_path / "fresh").as_posix()
                   for p in (tmp_path / "fresh").rglob("*") if p.is_file())  # fmt: skip
    assert found == KEPT
    for name in KEPT[:-1]:
        assert (tmp_path / "fresh" / name).read_text() == (tmp_path / "data" / name).read_text()
    assert rows(tmp_path / "fresh" / "users.db", "users") == 1


def test_a_restore_never_overwrites_without_being_told_and_never_deletes(tmp_path, capsys):
    folder = backed_up(tmp_path)
    live = tmp_path / "live"
    live.mkdir()
    (live / "users.db").write_text("today's data")
    (live / "events.db-wal").write_text("stale")
    with pytest.raises(RestoreError, match="is not empty"):
        restore(folder, live, replace=False, now=NOW)
    assert (live / "users.db").read_text() == "today's data"
    later = NOW.replace(hour=19)
    moved = restore(folder, live, replace=True, now=later)
    assert moved == tmp_path / "live.before-restore-20261014T190000Z"
    assert (moved / "users.db").read_text() == "today's data"  # kept, not deleted
    assert rows(live / "users.db", "users") == 1
    assert not (live / "events.db-wal").exists()  # a stale one would be replayed onto it
    assert restore_main(["--from", str(folder), "--to", str(live), "--replace"]) == 0
    out = capsys.readouterr().out
    assert "restore OK" in out and "is kept in" in out
    assert "MOCKTRADE_SECRET_KEY (from the password manager)" in out
    assert "log in to Kite again" in out


@pytest.mark.parametrize(
    "spoil, says",
    [
        (lambda f: (f / "rules/mtm-acc-1.json").write_text('{"position": {"x": 1}}'), "changed"),
        (lambda f: (f / "rules/mtm-acc-1.json").write_text('{"position": []}'), "changed"),  # size
        (lambda f: (f / "alerts/ab12.json").unlink(), "alerts/ab12.json is missing"),
        (lambda f: (f / "manifest.json").unlink(), "no manifest.json"),
        (lambda f: (f / "manifest.json").write_text("{"), "manifest.json is broken"),
        (lambda f: (f / "manifest.json").write_text('{"files": {}}'), "lists no database"),
    ],
)
def test_a_damaged_backup_is_refused_and_nothing_is_touched(tmp_path, spoil, says, capsys):
    folder = backed_up(tmp_path)
    spoil(folder)
    with pytest.raises(RestoreError, match=says):
        check_backup(folder)
    assert restore_main(["--from", str(folder), "--to", str(tmp_path / "fresh")]) == 1
    assert "restore REFUSED, nothing changed" in capsys.readouterr().err
    assert not (tmp_path / "fresh").exists()


def test_a_manifest_naming_files_outside_the_backup_is_refused(tmp_path):
    folder = backed_up(tmp_path)
    listed = json.loads((folder / "manifest.json").read_text())
    for evil in ("../../etc/passwd", "/etc/passwd"):
        listed["files"][evil] = {"bytes": 1, "sha256": "0" * 64}
        (folder / "manifest.json").write_text(json.dumps(listed))
        with pytest.raises(RestoreError, match="outside the backup"):
            check_backup(folder)
        del listed["files"][evil]


def test_a_database_that_fails_its_integrity_check_is_refused(tmp_path):
    folder = backed_up(tmp_path)
    db = folder / "users.db"
    raw = bytearray(db.read_bytes())
    raw[200:4000] = b"\xff" * 3800  # damaged inside, yet the manifest is made to agree
    db.write_bytes(bytes(raw))
    listed = json.loads((folder / "manifest.json").read_text())
    from mocktrade.tools.backup import sha256

    listed["files"]["users.db"] = {"bytes": db.stat().st_size, "sha256": sha256(db)}
    (folder / "manifest.json").write_text(json.dumps(listed))
    with pytest.raises(RestoreError, match="users.db fails SQLite's integrity check"):
        check_backup(folder)
