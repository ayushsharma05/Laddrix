"""#275 mutation check: break one rule of exact fill economics (Kite's average kept to the
fraction; a fill worked out from averages keeps its exact value; a rounded average that agrees
changes nothing; a corrected average with nothing new traded is a CORRECTED event; positions
take exact values and corrections, on the cost while held and on realised P&L once closed;
corrections are read back after a restart); the tests must notice. Runs the tests unmutated
first. Restores each file. Run alone."""
import subprocess

M = "src/mocktrade/orders/manager.py"
K = "src/mocktrade/broker/kite/orders.py"
B = "src/mocktrade/positions/book.py"
E = "src/mocktrade/store/events.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_exact_fills.py", "tests/test_order_manager.py", "tests/test_positions.py"]
MUTANTS = [
 (K, "            avg_price_exact=exact_paise(order.get(\"average_price\")),\n            reason=f", "            reason=f"),
 (K, "        avg_price_exact=exact_paise(order.get(\"average_price\")),\n        reason=reason,", "        reason=reason,"),
 (M, "        return round(exact * update.filled_qty)", "        return update.avg_price_paise * update.filled_qty"),
 (M, "        if abs(Fraction(known, order.filled_qty) - update.avg_price_paise) <= Fraction(1, 2):", "        if False:"),
 (M, "            if update.filled_qty == order.filled_qty and terms is None and not corrected:", "            if update.filled_qty == order.filled_qty and terms is None:"),
 (M, "        return Fill(order.request.client_order_id, qty, round(Fraction(new, qty)), False, new)", "        return Fill(order.request.client_order_id, qty, round(Fraction(new, qty)), False)"),
 (M, "        order.filled_value_paise = value\n", "\n"),
 (B, '        else:\n            price = Fraction(value, qty)\n', '        else:\n            pass\n'),
 (B, "            position.cost += side.sign * delta_paise  # still held: its cost moves", "            position.realised -= side.sign * delta_paise"),
 (B, "            position.realised -= side.sign * delta_paise  # closed: the profit moves", "            position.cost += side.sign * delta_paise"),
 (B, "        if event.kind not in (EventKind.FILL, EventKind.CORRECTED):", "        if event.kind is not EventKind.FILL:"),
 (E, "            (account_id, EventKind.FILL.value, EventKind.CORRECTED.value),", "            (account_id, EventKind.FILL.value, EventKind.FILL.value),"),
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
