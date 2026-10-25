"""#363 mutation check: break one rule of step-up and the session list (which changes ask,
which never do, the 5-minute window, a login counting, wrong codes locking and ending every
session, a code working once, only your own sessions, no wildcards); the tests must notice.
Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

U = "src/mocktrade/server/users.py"
A = "src/mocktrade/server/admin.py"
ROUTES_RISK = "src/mocktrade/server/routes/risk.py"
ROUTES_SESSION = "src/mocktrade/server/routes/session.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_step_up.py"]
MUTANTS = [
 (U, "        return self._clock() - datetime.fromisoformat(row[\"stepped_up_at\"]) <= STEP_UP_FOR", "        return True"),
 (U, "                        db.execute(\"DELETE FROM sessions WHERE user_id = ?\", (row[\"id\"],))", "                        pass"),
 (U, "                    if failures >= MAX_FAILURES:", "                    if False:"),
 (U, "                    db.execute(\"UPDATE users SET failures = 0, last_totp_step = ? WHERE id = ?\",", "                    db.execute(\"UPDATE users SET failures = 0, last_totp_step = last_totp_step + 0 * ? WHERE id = ?\","),
 (U, "            return db.execute(\"DELETE FROM sessions WHERE user_id = ? AND token_hash LIKE ?\",", "            return db.execute(\"DELETE FROM sessions WHERE ? IS NOT NULL AND token_hash LIKE ?\","),
 (U, "        if len(session_id) != SESSION_ID or not all(c in \"0123456789abcdef\" for c in session_id):", "        if False:"),
 (U, "                 \"current\": r[\"token_hash\"] == mine}", "                 \"current\": False}"),
 (ROUTES_SESSION, "                                    stepped_up=True)  # it just checked the code  # fmt: skip", "                                    stepped_up=False)"),
 (ROUTES_RISK, "        require_step_up(request, \"resume the whole firm\")", "        pass"),
 (A, "        step_up(request, \"add a user\")", "        pass"),
 (A, "            step_up(request, \"change roles or turn a user on\")", "            pass"),
 (A, "        step_up(request, \"reset a password\")", "        pass"),
 (A, "        step_up(request, \"reset an authenticator\")", "        pass"),
 (A, "        step_up(request, \"add an account\")", "        pass"),
 (A, "        if body.traders is not None or body.active is True:  # turning off never asks", "        if body.traders is not None:"),
 (A, "        if any(c[\"looser\"] for c in changed) and not dry_run:  # tightening never asks", "        if changed:"),
 (A, "        if body.roles is not None or body.active is True:  # what someone may do: privileged", "        if body.roles is not None or body.active is not None:"),
]
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
