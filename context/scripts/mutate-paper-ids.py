"""#419 mutation check: the paper broker's ids must stay unique across a restart (each start's
ids carry its start time) and the same for a replay with a fixed clock; the tests must notice
when either rule breaks. Restores each file in `finally`. Run alone from the repo root."""
import subprocess

P = "src/laddrix/broker/paper.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_paper_broker.py", "tests/test_recovery.py"]
MUTANTS = [
 (P, '        broker_id = f"P{self.run_id}-{seq}"', '        broker_id = f"P{seq}"'),
 (P, 'Trade(f"T{self.run_id}-{next(self._trade_ids)}"', 'Trade(f"T{next(self._trade_ids)}"'),
 (P, "        self.run_id = _base36(int(clock().timestamp()))", "        self.run_id = \"x\""),
 (P, "        self.run_id = _base36(int(clock().timestamp()))", "        self.run_id = _base36(id(self))"),
 (P, "        n, d = divmod(n, 36)", "        n, d = divmod(n, 10)"),
]
if subprocess.run(TESTS, capture_output=True, timeout=600).returncode != 0:
    raise SystemExit("the tests fail before any mutation: fix that first")
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
    if survived: print("SURVIVED:", f, a[:90], "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
