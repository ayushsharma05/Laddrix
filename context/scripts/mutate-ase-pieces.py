"""#422 mutation check: break one rule of the Autospreader's pieces (hedges split to fit one
order, the quote one piece of whole spread lots at a time, a resting quote too big at its new
price pulled, the start check in words, the risk gate's lots and value limits, the platform
wiring); the tests must notice. Restores each file in `finally`; a hang counts as caught.
Run alone from the repo root."""
import subprocess

A = "src/mocktrade/algos/autospreader.py"
R = "src/mocktrade/risk/checks.py"
E = "src/mocktrade/algos/engine.py"
P = "src/mocktrade/platform.py"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_autospreader.py", "tests/test_risk_checks.py",
       "tests/test_spread_api.py"], ".")
MUTANTS = [
 # hedges in pieces
 (A, "                piece = need if cap is None else min(need, cap)", "                piece = need", PY),
 (A, "                need -= piece", "                need = 0", PY),
 (A, "            cap = self._cap(ctx, leg.instrument_id, price)\n", "            cap = self._cap(ctx, leg.instrument_id, None)\n", PY),
 (A, "            if cap == 0:\n                ctx.say(f\"UNHEDGED", "            if False:\n                ctx.say(f\"UNHEDGED", PY),
 # the quote, one piece at a time
 (A, "            want = ctx.remaining() if piece is None else min(ctx.remaining(), piece)", "            want = ctx.remaining()", PY),
 (A, "        return cap // unit * unit", "        return cap", PY),
 (A, "        unit = abs(self.q.ratio) * ctx.instrument().lot_size", "        unit = ctx.instrument().lot_size", PY),
 (A, "            if piece is not None and c.leaves_qty > piece:", "            if False:", PY),
 (A, "        piece = self._piece(ctx, price)\n", "        piece = self._piece(ctx, None)\n", PY),
 # the start check
 (A, "        if why := self._too_big(ctx):\n            return ctx.fail(why)\n", "", PY),
 (A, "        if self._piece(ctx, self._quote_price(ctx)) == 0:", "        if False:", PY),
 (A, "            if self._cap(ctx, leg.instrument_id, level.price_paise if level else None) == 0:", "            if False:", PY),
 # the risk gate
 (R, "            lots = min(lots, self.limits.max_order_value_paise // one_lot)", "            lots = lots", PY),
 (R, "            one_lot = price_paise * instrument.lot_size", "            one_lot = price_paise", PY),
 (R, "        return max(0, lots) * instrument.lot_size", "        return max(1, lots) * instrument.lot_size", PY),
 (R, "        if instrument is None:\n            return 0\n        lots = self.limits", "        if instrument is None:\n            return 750\n        lots = self.limits", PY),
 # the wiring
 (E, "        return self._engine.order_cap(instrument_id or self._parent.instrument_id, price_paise)", "        return None", PY),
 (P, "                                  order_cap=risk.max_order_qty)  # fmt: skip", "                                  )  # fmt: skip", PY),
]
for cmd, cwd in (PY,):
    if subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd).returncode != 0:
        raise SystemExit(f"the tests fail before any mutation ({' '.join(cmd)}): fix that first")
caught = 0
for f, a, b, (cmd, cwd) in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
