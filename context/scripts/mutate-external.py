"""#355 mutation check: break one rule of adopting trades made outside MockTrade (only the
broker's trades no order of ours placed, each trade id once, never one already adopted; only
when they explain the difference exactly; a reason required; at each trade's own price;
audited; re-applied after a restart; risk or admin only); the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

X = "src/mocktrade/positions/external.py"
R = "src/mocktrade/recovery.py"
A = "src/mocktrade/server/app.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_external.py"]
MUTANTS = [
 (X, "        if (t.instrument_id, t.product) == (instrument_id, product) and not t.client_order_id:", "        if (t.instrument_id, t.product) == (instrument_id, product):"),
 (X, "            seen[t.trade_id] = t", "            seen[t.trade_id + str(len(seen))] = t"),
 (X, "    return [t for tid, t in seen.items() if tid not in adopted]", "    return list(seen.values())"),
 (X, "    if trades and outside == gap:", "    if trades:"),
 (X, "        if len(acknowledgement.strip()) < 10:", "        if False:"),
 (X, "            self._book.apply_fill(t.instrument_id, t.product, t.side, t.qty, t.price_paise)", "            self._book.apply_fill(t.instrument_id, t.product, t.side, t.qty, 2501000)"),
 (X, "            self._mine.add(t.trade_id)", "            pass"),
 (R, "        if e.ts >= since:\n            x = e.extra\n            book.apply_fill", "        if False:\n            x = e.extra\n            book.apply_fill"),
 (A, "        if not _risk_or_admin(user):\n            raise HTTPException(403, \"only risk or an admin can resolve", "        if False:\n            raise HTTPException(403, \"only risk or an admin can resolve"),
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
