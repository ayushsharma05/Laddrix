"""#291 mutation check: break one rule of the exchange algo id (ids from the settings per
code; an algo's orders carry its id; without one, no algo above 10 orders a second; the
throttle says its per-second limit; the screens are told; the server refuses ids for algos
it doesn't have); the tests must notice. Runs the tests unmutated first. Restores each file.
Run alone."""
import subprocess

E = "src/mocktrade/algos/engine.py"
T = "src/mocktrade/risk/throttle.py"
V = "src/mocktrade/tools/serve.py"
A = "src/mocktrade/server/app.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_algo_id.py", "tests/test_kite_orders.py"]
MUTANTS = [
 (E, "        if not kind.exchange_algo_id and self._ops() > OPS_WITHOUT_ALGO_ID:", "        if False:"),
 (E, "        if not kind.exchange_algo_id and self._ops() > OPS_WITHOUT_ALGO_ID:", "        if not kind.exchange_algo_id and self._ops() >= OPS_WITHOUT_ALGO_ID:"),
 (E, "    return [replace(t, exchange_algo_id=ids.get(t.spec.code) or t.exchange_algo_id)", "    return [t"),
 (E, "            algo_id=p.exchange_algo_id,", "            algo_id=None,"),
 (E, "        ids[code.strip().upper()] = value.strip()", "        ids[code.strip()] = value.strip()"),
 (T, "        self.per_second = min((r.limit for r in rules if r.window_s == 1.0), default=0)", "        self.per_second = 8"),
 (V, "        if unknown:\n", "        if False:\n"),
 (A, "             \"exchange_algo_note\": algo_id_note(t)}", "             }"),
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
