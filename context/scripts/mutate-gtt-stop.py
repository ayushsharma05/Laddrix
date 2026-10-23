"""#276 mutation check: break one rule of GTTs while trading is stopped (keep GTTs whose
every leg reduces the position; delete those that could open, at STOP and on each poll; say
a failed delete and try again; cancel the order of one that fires in STOP and adds risk;
nothing without STOP); the tests must notice. Runs the tests unmutated first. Restores each
file. Run alone."""
import subprocess

G = "src/mocktrade/orders/gtt.py"
P = "src/mocktrade/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_gtt_stop.py", "tests/test_gtt.py"]
MUTANTS = [
 (G, "        return all(_reduces(leg, held) for leg in view.request.legs)", "        return True"),
 (G, "        return all(_reduces(leg, held) for leg in view.request.legs)", "        return False"),
 (G, "    return held != 0 and leg.side.sign == -(1 if held > 0 else -1) and leg.qty <= abs(held)", "    return held != 0"),
 (G, "        if why is None:\n            return []", "        if True:\n            return []"),
 (G, "                done.append(words)\n                continue", "                raise"),
 (G, "            await self._orders.cancel(adopted.client_order_id)", "            pass"),
 (G, "        await self.sweep()\n", "\n"),
 (P, "s.name for s in (self.firm_switch, switch)", "s.name for s in (switch,)"),
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
