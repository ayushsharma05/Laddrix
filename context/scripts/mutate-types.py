"""#440 mutation check: the few rules the type clean-up wrote down (one price source watches
every alert; no protection given means the automatic one; a cancel goes only with the broker's
id; a strategy's done() is asked, not guessed; a basket's earlier legs count as working); the
tests must notice. Restores each file in `finally`; a hang counts as caught. Run alone from the
repo root."""
import subprocess

PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_market_alerts.py",
       "tests/test_paper_broker.py", "tests/test_order_manager.py", "tests/test_algo_engine.py",
       "tests/test_basket.py", "tests/test_orders.py"], ".")
MUTANTS = [
 ("src/mocktrade/alerts/engine.py", "            return self.watched()", "            return set()", PY),
 ("src/mocktrade/broker/rules.py", "    if protection_pct is None or protection_pct == -1:", "    if protection_pct == -1:", PY),
 ("src/mocktrade/orders/manager.py", "        await self._send_cancel(order, order.broker_order_id)", "        pass", PY),
 ("src/mocktrade/algos/engine.py", "            return bool(p.strategy.done(AlgoContext(self, p)))", "            return False", PY),
 ("src/mocktrade/risk/checks.py", "    return lambda: [*real(), *extra]", "    return lambda: [*real()]", PY),
]
for cmd, cwd in (PY,):
    if subprocess.run(cmd, capture_output=True, timeout=900, cwd=cwd).returncode != 0:
        raise SystemExit(f"the tests fail before any mutation ({' '.join(cmd)}): fix that first")
caught = 0
for f, a, b, (cmd, cwd) in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        survived = subprocess.run(cmd, capture_output=True, timeout=900, cwd=cwd).returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
