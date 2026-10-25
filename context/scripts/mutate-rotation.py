"""#364 mutation check: break one rule of key rotation and session revocation (all or nothing,
read back, refused while a server runs or with a wrong key, sessions ended for one user or all,
open streams closed within seconds, backups hold no session); the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

U = "src/laddrix/server/users.py"
T = "src/laddrix/tools/users.py"
S = "src/laddrix/server/stream.py"
B = "src/laddrix/tools/backup.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_key_rotation.py"]
MUTANTS = [
 (U, "            db.executemany(\"UPDATE users SET totp_secret = ? WHERE id = ?\", sealed)", "            pass"),
 (U, "                if new.open(again) != plain:", "                if False:"),
 (U, "        self._box = new\n", ""),
 (U, "                return db.execute(\"DELETE FROM sessions\").rowcount", "                return 0"),
 (U, "            return db.execute(\"DELETE FROM sessions WHERE user_id = ?\", (user_id,)).rowcount", "            return db.execute(\"DELETE FROM sessions\").rowcount"),
 (U, "                bad.append(row[\"id\"])", "                pass"),
 (T, "    if new == env.get(\"LADDRIX_SECRET_KEY\"):", "    if False:"),
 (T, '    fence = DataFence(store.path.parent)  # the server holds this while it runs (#273)\n    try:\n        fence.acquire()', '    fence = DataFence(store.path.parent)  # the server holds this while it runs (#273)\n    try:\n        pass'),
 (T, "    if check:\n", "    if False:\n"),
 (T, "    if bad := store.check_key():", "    if False:"),
 (S, "            if not due and digest in self._live_sessions():", "            if not due:"),
 (B, "                    dst.execute(f\"DELETE FROM {table}\")", "                    pass"),
]
# Not mutated: `PRAGMA secure_delete = ON` in backup.py. This SQLite build already has it on by
# default, so removing it changes nothing here; it is there for builds where it is off.
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
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
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
