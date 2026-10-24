"""#427 mutation check: break one rule of several quoting legs (the definition allowing one or
more, each order's role and its codec, telling quotes from hedges, the targets from quote
fills, the lots shared out and never overfilled, each leg's own quote price and rounding,
every leg hedgeable, only quotes pulled on stop, the form's tick boxes); the tests must
notice. Restores each file in `finally`; a hang counts as caught. Run alone from the repo
root."""
import subprocess

A = "src/mocktrade/algos/autospreader.py"
M = "src/mocktrade/spreads/model.py"
C = "src/mocktrade/orders/codec.py"
E = "src/mocktrade/algos/engine.py"
FM = "ui/src/widgets/ase/model.ts"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_ase_multi_quote.py", "tests/test_autospreader.py",
       "tests/test_ase_controls.py", "tests/test_algo_restore.py", "tests/test_spreads.py",
       "tests/test_spread_api.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/ase"], "ui")
MUTANTS = [
 # the definition, roles
 (M, "    if not any(leg.quote for leg in legs):", "    if sum(leg.quote for leg in legs) != 1:", PY),
 (C, "        algo_role=str(d.get(\"algo_role\", \"\")),  # absent before #427", "        algo_role=\"\",  # absent before #427", PY),
 (E, "            algo_role=role,\n", "", PY),
 (A, "        if r.algo_role:\n            return r.algo_role == \"quote\"", "        if False:\n            return r.algo_role == \"quote\"", PY),
 (A, "                                       role=\"quote\"):  # fmt: skip", "                                       role=\"hedge\"):  # fmt: skip", PY),
 # targets from quote fills
 (A, "            if q is leg:\n                target += self._quoted(ctx, q)", "            if q is leg:\n                target += 0", PY),
 (A, "                target += q_lots * abs(leg.ratio) // abs(q.ratio) * lot", "                target += q_lots * lot", PY),
 (A, "        for leg in self.legs:\n            need = self._target(ctx, leg) - self._net(ctx, leg)", "        for leg in self.others:\n            need = self._target(ctx, leg) - self._net(ctx, leg)", PY),
 (A, "        return filled - ctx.filled(leg.instrument_id, _other_side(self._side(leg)))", "        return filled", PY),
 # sharing the lots
 (A, "            share = free / (len(idle) - n)", "            share = free", PY),
 (A, "            if not lots and free * abs(q.ratio) >= 1:", "            if False:", PY),
 (A, "                free -= Fraction(want, abs(q.ratio) * lot)", "                pass", PY),
 (A, "            qty = self._quoted(ctx, q) + working + self.quote_in_flight[q.instrument_id]", "            qty = self._quoted(ctx, q) + working", PY),
 (A, "        if idle and not self._short(ctx):", "        if idle:", PY),
 # each leg's own quote price
 (A, "            if leg is quote:\n                continue\n            level = self._best(ctx, leg)", "            if leg is self.q:\n                continue\n            level = self._best(ctx, leg)", PY),
 (A, "        return self.buying == ((quote or self.q).multiplier > 0)", "        return self.buying == (self.q.multiplier > 0)", PY),
 # hedging every leg, covering not counting quotes
 (A, "            and not self._is_quote(c)\n        )  # fmt: skip", "        )  # fmt: skip", PY),
 (A, "        self._sync(ctx)\n        for leg in self.legs:\n            side = self._side(leg)\n            need =", "        self._sync(ctx)\n        for leg in self.others:\n            side = self._side(leg)\n            need =", PY),
 (A, "        return not self._is_quote(child)", "        return child.request.instrument_id != self.q.instrument_id", PY),
 (A, "        return self._lots_done(ctx) >= self._lots(ctx) and not self._short(ctx)", "        return self._lots_done(ctx) >= 1 and not self._short(ctx)", PY),
 # the form
 (FM, "  if (turnOff && d.legs.filter((l) => l.quote).length <= 1) return d;", "", UI),
 (FM, "  return { ...d, legs: d.legs.map((l, i) => (i === index ? { ...l, quote: !turnOff } : l)) };", "  return { ...d, legs: d.legs.map((l, i) => ({ ...l, quote: i === index })) };", UI),
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
