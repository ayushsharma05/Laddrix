"""#277 mutation check: break one rule of "data that says what it is" (the tape's epoch and its
estimate words, the movers' universe, a report's mark times and "prices as of", the candle
note) or of the bad-morning drill (a lost answer held, never re-sent; the disk check; the price
age check); the tests must notice. Runs the tests unmutated first. Restores each file. Run
alone."""
import subprocess

P = "src/laddrix/marketdata/prints.py"
ROUTES_MARKET = "src/laddrix/server/routes/market.py"
M = "src/laddrix/marketdata/movers.py"
D = "src/laddrix/reports/daily.py"
B = "src/laddrix/research/backtest.py"
O = "src/laddrix/orders/manager.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_truthful_data.py"]
MUTANTS = [
 (P, "        self.epoch = secrets.token_hex(4)", "        self.epoch = \"same-run\""),
 (ROUTES_MARKET, "                \"epoch\": mine.prints.epoch,", ""),
 (ROUTES_MARKET, "the time is \"\n                            \"the snapshot's last trade,", "\"\n                            \""),
 (M, "            \"universe\": f\"ranked among the {len(rows)} contracts", "            \"universe\": f\"ranked among the contracts"),
 (D, "            marked_at[p.instrument_id] = tick.received_at", "            marked_at[p.instrument_id] = None"),
 (D, "    marked = [p.mark_at for p in r.positions if p.mark_at is not None and p.closing_qty]", "    marked = []"),
 (D, "                \"mark_at\": p.mark_at.isoformat() if p.mark_at else None,", "                \"mark_at\": None,"),
 (B, "        if self.candles:\n            out[\"fills_note\"] = CANDLE_NOTE", "        if True:\n            out[\"fills_note\"] = CANDLE_NOTE"),
 (B, "    \"extreme, close) with one tick of spread, not what traded. Fills are an \"", "    \"extreme, close) with one tick of spread. Fills are an \""),
 ("src/laddrix/risk/checks.py", "        if age is None or age <= self.max_price_age_s:", "        if True:"),
 (O, "        if (problem := await self._make_durable(None)) is not None:", "        if (problem := await self._make_durable(None)) is not None and False:"),
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
