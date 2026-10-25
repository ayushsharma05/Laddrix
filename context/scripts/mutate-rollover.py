"""#266 mutation check: break one rule of the new-trading-day rollover (positions carried at
the last price with today's totals at zero and their old average kept; flat rows and delivery
buys dropped; day orders expired; charges again; once a day; today already built at start);
the tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

B = "src/laddrix/positions/book.py"
M = "src/laddrix/orders/manager.py"
P = "src/laddrix/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_rollover.py", "tests/test_positions.py"]
MUTANTS = [
 (B, "            reference = self._ltp.get(instrument_id) or round(p.avg)", "            reference = round(p.avg)"),
 (B, "            if not p.qty or (product is Product.CNC and p.qty > 0):", "            if not p.qty:"),
 (B, "                carried_avg=p.carried_avg if p.carried_avg is not None else p.avg,", "                carried_avg=None,"),
 (B, "        self._positions = carried\n", "        self._positions.update(carried)\n"),
 (M, "            if order.status.is_final or order.created_at >= before:", "            if order.status.is_final:"),
 (M, "            order.status = OrderStatus.CANCELLED\n            order.pending = Pending.NONE\n            order.reason = \"expired", "            order.pending = Pending.NONE\n            order.reason = \"expired"),
 (P, "            if runtime.session_day is not None and runtime.session_day >= today:", "            if False:"),
 (P, "            if runtime.charges is not None:\n                runtime.charges.refresh()", "            if False:\n                runtime.charges.refresh()"),
 (P, "        runtime.session_day = clock().astimezone(IST).date()  # a start rebuilds today (recovery)", "        runtime.session_day = None"),
 (P, "            runtime.session_day = today\n", "            pass\n"),
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
