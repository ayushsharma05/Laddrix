"""#415 mutation check: break one rule of the Autospreader algo (the quote's price and
rounding, the sides of each leg, re-quoting within the budget, stale and missing legs, hedge
sizes in whole lots, idempotent hedging, pay-up, keeping hedges on stop, the unhedged alert,
the frozen definition, ownership, whole spread lots, the trade route); the tests must notice.
Restores each file in `finally`; a hang counts as caught. Run alone from the repo root."""
import subprocess

A = "src/mocktrade/algos/autospreader.py"
P = "src/mocktrade/server/app.py"
F = "ui/src/widgets/algos/model.ts"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_autospreader.py", "tests/test_spread_api.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/algos"], "ui")
MUTANTS = [
 # the quote
 (A, "        exact = Fraction(self.level - rest, self.q.multiplier * tick_size)  # in Q's ticks", "        exact = Fraction(self.level + rest, self.q.multiplier * tick_size)  # in Q's ticks", PY),
 (A, "        return self.buying == (self.q.multiplier > 0)", "        return self.buying", PY),
 (A, "        price = (math.floor(exact) if self._down() else math.ceil(exact)) * tick_size", "        price = round(exact) * tick_size", PY),
 (A, "            rest += leg.multiplier * level.price_paise", "            rest += level.price_paise", PY),
 (A, "        return tick.best_ask if (side or self._side(leg)) is Side.BUY else tick.best_bid", "        return tick.best_bid if (side or self._side(leg)) is Side.BUY else tick.best_ask", PY),
 (A, "        return Side.BUY if (leg.ratio > 0) == self.buying else Side.SELL", "        return Side.BUY if leg.ratio > 0 else Side.SELL", PY),
 (A, "        self.buying = (ctx.side is Side.BUY) == (self.q.ratio > 0)", "        self.buying = ctx.side is Side.BUY", PY),
 (A, "        if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:", "        if False:", PY),
 (A, "            if level is None:\n                return None", "            if level is None:\n                continue", PY),
 (A, "            if current == price or current is None or c.pending is not Pending.NONE:", "            if current is None or c.pending is not Pending.NONE:", PY),
 (A, "            if done >= MODIFY_BUDGET:", "            if done > MODIFY_BUDGET:", PY),
 (A, "            for c in quotes:\n                ctx.cancel(c.client_order_id)  # no hedge price", "            for c in []:\n                ctx.cancel(c.client_order_id)  # no hedge price", PY),
 (A, "            elif want > 0 and not self._short(ctx) and ctx.send(want, price):", "            elif want > 0 and ctx.send(want, price):", PY),
 # hedging
 (A, "        lots = q_lots * abs(leg.ratio) // abs(self.q.ratio)", "        lots = q_lots * abs(leg.ratio)", PY),
 (A, "        lots = q_lots * abs(leg.ratio) // abs(self.q.ratio)", "        lots = -(-q_lots * abs(leg.ratio) // abs(self.q.ratio))", PY),
 (A, "            need = short.get(leg.instrument_id, 0) - self._covering(ctx, leg, side)", "            need = short.get(leg.instrument_id, 0)", PY),
 (A, "            self.in_flight[leg.instrument_id] += piece", "", PY),
 (A, "                self.in_flight[leg] = max(0, self.in_flight[leg] - c.request.qty)", "", PY),
 (A, "            step = ctx.instrument(leg.instrument_id).tick_paise * leg.payup_ticks", "            step = ctx.instrument(leg.instrument_id).tick_paise * 2", PY),
 (A, "            price = level.price_paise + (step if side is Side.BUY else -step)", "            price = level.price_paise - (step if side is Side.BUY else -step)", PY),
 (A, "                        check_remaining=False, hedge=True", "                        check_remaining=False, hedge=False", PY),
 (A, "        self._hedge(ctx)  # on every update", "        self._hedge(ctx) if event.kind.name == 'FILL' else None  # on every update", PY),
 (A, "        return not (r.instrument_id == self.q.instrument_id and r.side is ctx.side)", "        return False", PY),
 (A, "        return self._net(ctx, self.q) >= ctx.qty and not self._short(ctx)", "        return self._net(ctx, self.q) >= ctx.qty", PY),
 (A, "        if not short:\n            return None\n        legs =", "        if True:\n            return None\n        legs =", PY),
 # the definition and its owner
 (A, "        if self.spread.user_id != ctx.user_id:", "        if False:", PY),
 (A, "        if ctx.instrument_id != self.q.instrument_id:", "        if False:", PY),
 (A, "        if ctx.qty % (abs(self.q.ratio) * lot):", "        if ctx.qty % lot:", PY),
 # the report
 (A, "            traded_spread_paise=traded if complete else None,", "            traded_spread_paise=traded,", PY),
 (A, "            unhedged=sum(short.values()),", "            unhedged=len(short),", PY),
 # the route
 (P, "        buys_q = (q.ratio > 0) == (body.side is Side.BUY)", "        buys_q = body.side is Side.BUY", PY),
 (P, "                qty=body.lots * abs(q.ratio) * instrument.lot_size,", "                qty=body.lots * instrument.lot_size,", PY),
 (P, "        spread = book.visible(user, PREFIX + spread_id)", "        spread = book.of_instrument(PREFIX + spread_id)", PY),
 # the algo screen
 (F, "  return specs.filter((s) => !STARTED_ELSEWHERE.has(s.family ?? \"execution\"));", "  return specs;", UI),
]
for cmd, cwd in (PY, UI):
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
