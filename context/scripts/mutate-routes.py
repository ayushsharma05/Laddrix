"""#441 mutation check: break one rule of the split API (each area's router is included, the
admin API gets the real step-up check, a broad route can't come before a narrow one, the
screens come last, the shared checks in server/context.py); the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

A = "src/mocktrade/server/app.py"
C = "src/mocktrade/server/context.py"
M = "src/mocktrade/server/routes/market.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_routes.py", "tests/test_server.py",
         "tests/test_workspaces.py", "tests/test_admin.py", "tests/test_step_up.py",
         "tests/test_operations.py", "tests/test_security_surface.py"]
MUTANTS = [
 (A, "    for area in AREAS:\n", "    for area in AREAS[1:]:\n"),  # ops: health, status, metrics
 (A, "    for area in AREAS:\n", "    for area in AREAS[:-1]:\n"),  # portfolio: positions, reports
 (A, "                                    ctx.require_step_up))  # fmt: skip",
     "                                    lambda request, what: None))  # fmt: skip"),
 (A, "            if path.startswith((\"api/\", \"ws\", \"kite/\")):", "            if False:"),
 (A, "            if request.headers.get(\"origin\") != settings.origin:", "            if False:"),
 (M, "    @api.get(\"/api/alerts/webhook\")\n",
     "    @api.get(\"/api/alerts/{alert_id}\")\n    async def early(alert_id: str) -> None:\n"
     "        return None\n\n    @api.get(\"/api/alerts/webhook\")\n"),
 (C, "        if user.must_change_password and request.url.path not in PASSWORD_FIRST:", "        if False:"),
 (C, "        if account is None or running is None or not can_view(user, account):",
     "        if account is None or running is None:"),
 (C, "        return account is not None and account.workspace == user.workspace", "        return account is not None"),
 (C, "        return found is not None and found.workspace == user.workspace", "        return found is not None"),
 (C, "        if not self.users.stepped_up(request.cookies.get(self.settings.cookie_name)):", "        if False:"),
]
if subprocess.run(TESTS, capture_output=True, timeout=600).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=600)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
