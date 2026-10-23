"""#366 mutation check: break one rule of the re-check before delayed actions (new orders and
changes refused once rights are gone; closes and cancels still go; an algo stops before its next
child; GTTs that could open are deleted, stop-losses kept; the server's check is the screens'
rule); the tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

A = "src/mocktrade/risk/access.py"
E = "src/mocktrade/algos/engine.py"
G = "src/mocktrade/orders/gtt.py"
P = "src/mocktrade/platform.py"
V = "src/mocktrade/tools/serve.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_access_recheck.py", "tests/test_gtt_stop.py"]
MUTANTS = [
 (A, "        if request.reduce_only or request.forced_close:\n            return None", "        if False:\n            return None"),
 (A, "        return self._why(request.user_id)", "        return None"),
 (A, "        return self._why(order.request.user_id)", "        return None"),
 (A, "        if not user.active:", "        if False:"),
 (A, "        if not can_manage(user, account):", "        if False:"),
 (A, "        if user is None or account is None:", "        if False:"),
 (A, "        return self._access()(user_id, self._account_id)", "        return None"),
 (E, "        if not hedge and not reduce_only and (why := self._engine.access(p.user_id)):", "        if False and (why := self._engine.access(p.user_id)):"),
 (G, "        if who := self._access(view.request.user_id):", "        if False:"),
 (G, "            if why is None:\n                continue", "            if True:\n                continue"),
 (V, "    platform.access = trading_rights(users)", "    pass"),
 (P, "        checks = (pause, rights, recovery,", "        checks = (pause, recovery,"),
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
