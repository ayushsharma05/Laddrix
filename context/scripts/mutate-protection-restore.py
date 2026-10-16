"""#264 mutation check: break one rule of durable lost-stop flags and the guarded Restore
(flags and resolutions written to the audit trail and rebuilt at start; Restore refused when
it could duplicate an exit; sized to what is open; a bracket as an OCO at its levels); the
tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

G = "src/mocktrade/ops/protection.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_protection_restore.py",
         "tests/test_protection_guard.py", "tests/test_protect.py"]
MUTANTS = [
 (G, "                if e.kind == FLAGGED:", "                if False:"),
 (G, "                else:\n                    self._flags.pop(where, None)\n                    self._lost.pop(where, None)", "                else:\n                    pass"),
 (G, "        self._note(where, FLAGGED, why,", "        (lambda *a: None)(where, FLAGGED, why,"),
 (G, "            self._note(where, RESOLVED, how or \"resolved\", {\"how\": how})", "            pass"),
 (G, "            if o.pending is Pending.UNCONFIRMED:\n                raise RestoreError", "            if False:\n                raise RestoreError"),
 (G, "            if r.parent_id is not None and r.parent_id not in live:", "            if False:"),
 (G, "                raise RestoreError(f\"{a.code} {a.algo_id} is still there", "                pass\n                (f\"{a.code} {a.algo_id} is still there"),
 (G, "        if open_qty <= 0:", "        if open_qty < -10**9:"),
 (G, "        qty = min(int(algo[\"qty\"]), open_qty)", "        qty = int(algo[\"qty\"])"),
 (G, "            exit_side = Side.SELL if exit_side is Side.BUY else Side.BUY", "            pass"),
 (G, "            stop = avg - up * int(params[\"stop_points_paise\"])", "            stop = avg + up * int(params[\"stop_points_paise\"])"),
 (G, "        if not algo:\n            raise RestoreError", "        if False:\n            raise RestoreError"),
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
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
