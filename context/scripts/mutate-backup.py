"""#329 mutation check: break one rule of backup and restore (what goes in, what never does, the
manifest, half backups, checking a backup, never overwriting or deleting, stale WAL files, what
the restore says, moving aside in place); the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

B = "src/mocktrade/tools/backup.py"
R = "src/mocktrade/tools/restore.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_backup.py"]
MUTANTS = [
 (B, 'FOLDERS = ("rules", "reports", "alerts", "candles", "backtests")', 'FOLDERS = ("rules", "reports", "candles", "backtests")'),
 (B, 'FOLDERS = ("rules", "reports", "alerts", "candles", "backtests")', 'FOLDERS = ("reports", "alerts", "candles", "backtests")'),
 (B, 'NEVER = ("kite_token_*.json", "*.key", ".env", "*.tmp", "*.part")', 'NEVER = ("*.key", ".env", "*.tmp", "*.part")'),
 (B, 'NEVER = ("kite_token_*.json", "*.key", ".env", "*.tmp", "*.part")', 'NEVER = ("kite_token_*.json", "*.key", ".env", "*.part")'),
 (B, "        if path.is_file() and not path.is_symlink() and not never(path):", "        if path.is_file() and not path.is_symlink():"),
 (B, '        for name in FOLDERS + (("ticks",) if with_ticks else ()):', '        for name in FOLDERS + ("ticks",):'),
 (B, '        for name in FOLDERS + (("ticks",) if with_ticks else ()):', '        for name in FOLDERS:'),
 (B, '    except BaseException:\n        shutil.rmtree(part, ignore_errors=True)\n        raise', '    except BaseException:\n        raise'),
 (B, '        dst.execute("PRAGMA journal_mode=DELETE")', '        pass'),
 (B, '    os.umask(0o077)  # backups', '    os.umask(0o022)  # backups'),
 (B, '    return {"format": FORMAT, "made_at": now.isoformat(), "parts": parts, "files": files}', '    return {"format": FORMAT, "made_at": now.isoformat(), "parts": [], "files": files}'),
 (R, '        if path.stat().st_size != expect["bytes"] or sha256(path) != expect["sha256"]:', '        if path.stat().st_size != expect["bytes"]:'),
 (R, '        if not path.is_file():\n            raise RestoreError(f"{folder}: {name} is missing")', '        if not path.is_file():\n            continue'),
 (R, '        if Path(name).is_absolute() or ".." in Path(name).parts:', '        if ".." in Path(name).parts:'),
 (R, '        if Path(name).is_absolute() or ".." in Path(name).parts:', '        if Path(name).is_absolute():'),
 (R, '    if not isinstance(files, dict) or not any(name in files for name in DATABASES):', '    if not isinstance(files, dict):'),
 (R, '            if result != "ok":', '            if False:'),
 (R, '                result = str(exc)  # too damaged to even check', '                raise'),
 (R, '        if not replace:\n            raise RestoreError(', '        if False:\n            raise RestoreError('),
 (R, '            path.rename(moved / path.name)', '            shutil.rmtree(path) if path.is_dir() else path.unlink()'),
 (R, '            path.rename(moved / path.name)', '            shutil.copy2(path, moved / path.name) if path.is_file() else None'),
 (R, 'LEFT_IN_PLACE = ("backups",)', 'LEFT_IN_PLACE = ()'),
 (R, '                   if p.name not in LEFT_IN_PLACE and not p.name.startswith(ASIDE)]', '                   if p.name not in LEFT_IN_PLACE]'),
 (R, '    listed = check_backup(source)\n    present = []', '    listed = json.loads((source / MANIFEST).read_text())\n    present = []'),
 (R, '    except (RestoreError, OSError) as exc:', '    except OSError as exc:'),
 (R, '    for item in FROM_ELSEWHERE:\n        print(f"  - {item}")', '    pass'),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90], "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
