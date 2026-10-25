"""#270 mutation check: break one rule of ownership (an account's alerts reach only those who
may see it, on connect and live, and its end too; backtests are their owner's, an admin sees
all, someone else's is 'no backtest'; old runs are admin-only; candles imported by admins only);
the tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

A = "src/laddrix/ops/alerts.py"
S = "src/laddrix/server/stream.py"
ROUTES_RESEARCH = "src/laddrix/server/routes/research.py"
B = "src/laddrix/research/backtest.py"
G = "src/laddrix/ops/guards.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_ownership.py", "tests/test_stream.py",
         "tests/test_backtest.py", "tests/test_ops.py", "tests/test_protection_guard.py"]
C = "src/laddrix/ops/checks.py"
R = "src/laddrix/ops/protection.py"
V = "src/laddrix/tools/serve.py"
MUTANTS = [
 (C, "                                account_id=account.id)  # fmt: skip", "                                )  # fmt: skip"),
 (R, "                account_id=account_id,\n            )\n            self._raised.add(where)", "            )\n            self._raised.add(where)"),
 (V, "                      account_id=account_id)  # fmt: skip", "                      )  # fmt: skip"),
 (G, "                            account_id=event.account_id)  # fmt: skip", "                            )  # fmt: skip"),
 (G, "                self._alerts.raise_(key, \"WARN\", text, account_id=account_id)", "                self._alerts.raise_(key, \"WARN\", text)"),
 (S, "    return alert.account_id is None or alert.account_id in connection.accounts", "    return True"),
 (S, "            if _may_see(connection, alert):\n                connection.put(alert_to_dict(alert))", "            if True:\n                connection.put(alert_to_dict(alert))"),
 (S, "            if _may_see(connection, alert):\n                connection.put(message)", "            if True:\n                connection.put(message)"),
 (A, "False, self._clock(), old.account_id", "False, self._clock(), None"),
 (B, "        return admin or self.owner == user_id", "        return True"),
 (ROUTES_RESEARCH, "            run = runs.start(spec, candles=body.candles, owner=user.id)", "            run = runs.start(spec, candles=body.candles)"),
 (ROUTES_RESEARCH, "        if run is None or not run.may_see(user.id, admin_of(user, run.owner)):", "        if run is None:"),
 (ROUTES_RESEARCH, "        own_run(run_id, user)\n", "\n"),
 (ROUTES_RESEARCH, "                if r.may_see(user.id, admin and admin_of(user, r.owner))]  # fmt: skip", "                ]  # fmt: skip"),
 (ROUTES_RESEARCH, "        if not user.has_role(Role.ADMIN):  # the firm's dataset", "        if False:  # the firm's dataset"),
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
