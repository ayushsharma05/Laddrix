"""#267 mutation check: break one rule of price freshness (algos and the paper broker see every
tick; bounded readers drop the oldest and count it; late ticks never replace newer state; risk
needs a fresh price per instrument for orders that add risk, closing goes; spread legs within a
skew); the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

S = "src/laddrix/marketdata/service.py"
C = "src/laddrix/risk/checks.py"
E = "src/laddrix/algos/engine.py"
R = "src/laddrix/algos/spreader.py"
O = "src/laddrix/ops/checks.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_freshness.py",
         "tests/test_market_data.py", "tests/test_spreader.py", "tests/test_risk_checks.py", "tests/test_paper_broker.py"]
P = "src/laddrix/broker/paper.py"
MUTANTS = [
 (P, "market_data.subscribe(every_tick=True)", "market_data.subscribe()"),
 (E, "market_data.subscribe(every_tick=True)", "market_data.subscribe()"),
 (S, "        if self.conflate:\n            self._pending[tick.instrument_id] = tick", "        if True:\n            self._pending[tick.instrument_id] = tick"),
 (S, "                self._queue.popleft()", "                pass"),
 (S, "                self._service.dropped += 1", "                pass"),
 (S, "        if then is not None and tick.exchange_time is not None and tick.exchange_time < then:", "        if False:"),
 (S, '        self._latest[tick.instrument_id] = tick\n        self._seen_at[tick.instrument_id] = self._clock()\n        self.vap.on_tick(tick)', '        self._latest[tick.instrument_id] = tick\n        self.vap.on_tick(tick)'),
 (C, "        if age is None or age <= self.max_price_age_s:", "        if True:"),
 (C, "        if request.reduce_only or self._closes(request, leaves, others):\n            return None\n        return (f\"the last price", "        if False:\n            return None\n        return (f\"the last price"),
 (C, "            or (self._stale_problem(instrument, new, leaves, others) if grows else None)", "            or None"),
 (R, "        if a_age is not None and b_age is not None and abs(a_age - b_age) > MAX_LEG_SKEW_S:", "        if False:"),
 (O, "        if dropped > 0:", "        if False:"),
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
