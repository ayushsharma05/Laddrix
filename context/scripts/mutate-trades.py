"""#352 mutation check: break one rule of checking orders against the broker's trade list
(each trade id once, a corrected trade replaces; same qty and a different value corrects the
order; a different qty is named after two reads, once, never changed; orders without fills
are left alone; charges follow the exact value and corrections; the platform runs it on each
trade list); the tests must notice. Runs the tests unmutated first. Restores each file. Run
alone."""
import subprocess

C = "src/laddrix/orders/trade_check.py"
M = "src/laddrix/orders/manager.py"
G = "src/laddrix/charges/model.py"
H = "src/laddrix/charges/service.py"
P = "src/laddrix/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_trade_check.py", "tests/test_charges.py"]
MUTANTS = [
 (C, "        by_id[(t.broker_order_id, t.trade_id)] = t", "        by_id[(t.broker_order_id, t.trade_id, t.price_paise)] = t"),
 (C, "        if value == view.filled_value_paise:\n            return []", "        if True:\n            return []"),
 (C, "            if self._seen.get(key) == gap and gap not in self.gaps:", "            if gap not in self.gaps:"),
 (C, "            if self._seen.get(key) == gap and gap not in self.gaps:", "            if self._seen.get(key) == gap:"),
 (M, "        order.filled_value_paise = value_paise\n        order.avg_price_paise", "        order.avg_price_paise"),
 (G, "                    getattr(view, \"filled_value_paise\", 0) or view.filled_qty * view.avg_price_paise", "                    view.filled_qty * view.avg_price_paise"),
 (H, "        if event.kind not in (EventKind.FILL, EventKind.CORRECTED):", "        if event.kind is not EventKind.FILL:"),
 (P, "            if snapshot.kind == \"trades\" and not snapshot.error:\n                check.check(snapshot.items)", "            pass"),
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
