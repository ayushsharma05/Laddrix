"""#414 mutation check: break one rule of spread definitions and their implied prices (which
side of each leg a buy uses, the multiplier, sizes in lots, missing and stale legs, the tick,
the checks in words, ownership, the disk, the pricer's watching and re-pricing, derived ticks
kept from the recorder, the API and the stream) or of the Autospreader's form; the tests
must notice. Restores each file in `finally`; a hang counts as caught. Run alone from the
repo root."""
import subprocess

M = "src/mocktrade/spreads/model.py"
S = "src/mocktrade/spreads/store.py"
P = "src/mocktrade/spreads/pricer.py"
D = "src/mocktrade/marketdata/service.py"
A = "src/mocktrade/server/app.py"
W = "src/mocktrade/server/stream.py"
U = "src/mocktrade/tools/users.py"
F = "ui/src/widgets/ase/model.ts"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_spreads.py", "tests/test_spread_api.py",
       "tests/test_personal_data.py", "tests/test_workspaces.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/ase"], "ui")
MUTANTS = [
 # the implied price
 (M, "            level = tick.best_ask if (leg.multiplier > 0) == buying else tick.best_bid", "            level = tick.best_ask if buying else tick.best_bid", PY),
 (M, "            price += leg.multiplier * level.price_paise", "            price += level.price_paise if leg.multiplier > 0 else -level.price_paise", PY),
 (M, "            here = level.qty // (abs(leg.ratio) * instrument.lot_size)", "            here = level.qty // instrument.lot_size", PY),
 (M, "            here = level.qty // (abs(leg.ratio) * instrument.lot_size)", "            here = level.qty // abs(leg.ratio)", PY),
 (M, "            lots = here if lots is None else min(lots, here)", "            lots = here if lots is None else max(lots, here)", PY),
 (M, "        return DepthLevel(price, lots or 0, 0) if lots else None", "        return DepthLevel(price, lots or 0, 0)", PY),
 (M, "    return Implied(side(False), side(True), ltp, \"\", received)", "    return Implied(side(True), side(False), ltp, \"\", received)", PY),
 (M, "    if len(have) < len(ticks):\n        return Implied(None, None, ltp, \"\", received)", "    if not have:\n        return Implied(None, None, ltp, \"\", received)", PY),
 (M, "    if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:", "    if False:", PY),
 (M, "    if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:", "    if ages and max(ages) - min(ages) >= MAX_LEG_SKEW_S:", PY),
 (M, "            step = math.gcd(step, abs(leg.multiplier) * instrument.tick_paise)", "            step = max(step, instrument.tick_paise)", PY),
 (M, "        bids=(now.bid,) if now.bid else (),\n        asks=(now.ask,) if now.ask else (),", "        bids=(now.ask,) if now.ask else (),\n        asks=(now.bid,) if now.bid else (),", PY),
 # the checks
 (M, "    if not clean or len(clean) > MAX_NAME:", "    if len(clean) > MAX_NAME:", PY),
 (M, "    if not MIN_LEGS <= len(legs) <= MAX_LEGS:", "    if len(legs) < MIN_LEGS:", PY),
 (M, "    if len(set(ids)) != len(ids):", "    if False:", PY),
 (M, "        elif not instrument.tradable:", "        elif False:", PY),
 (M, "        elif expired(instrument):", "        elif False:", PY),
 (M, "        if leg.ratio == 0 or abs(leg.ratio) > MAX_RATIO:", "        if leg.ratio == 0:", PY),
 (M, "        elif leg.ratio and (leg.ratio > 0) != (leg.multiplier > 0):", "        elif False:", PY),
 (M, "        if not 0 <= leg.payup_ticks <= MAX_PAYUP:", "        if leg.payup_ticks < 0:", PY),
 (M, "    if not any(leg.quote for leg in legs):", "    if False:", PY),
 # the book
 (S, "    return spread.user_id == user.id and spread.workspace == user.workspace", "    return spread.user_id == user.id", PY),
 (S, "        if len(self.mine(user)) >= MAX_SPREADS_PER_USER:", "        if len(self.mine(user)) > MAX_SPREADS_PER_USER:", PY),
 (S, "        if old is None or not _owns(user, old):\n            raise SpreadError([f\"no spread {spread_id}\"], missing=True)\n        problems", "        if old is None:\n            raise SpreadError([f\"no spread {spread_id}\"], missing=True)\n        problems", PY),
 (S, "        if old is None or not _owns(user, old):\n            raise SpreadError([f\"no spread {spread_id}\"], missing=True)\n        del", "        if old is None:\n            raise SpreadError([f\"no spread {spread_id}\"], missing=True)\n        del", PY),
 (S, "                           old.created_at, self._clock(), controls or old.controls)  # fmt: skip", "                           self._clock(), self._clock(), controls or old.controls)  # fmt: skip", PY),
 (S, "        os.replace(tmp, path)", "", PY),
 (S, "        spreads = [spread_to_dict(s) for s in self._by_id.values() if s.user_id == user_id]", "        spreads = [spread_to_dict(s) for s in self._by_id.values()]", PY),
 (S, "        self._tell(spread_id, spread)\n        return spread\n\n    def delete", "        return spread\n\n    def delete", PY),
 (S, "        self._tell(spread_id, None)", "", PY),
 # the pricer and derived ticks
 (P, "        self._legs[spread.spread_id] = self._prices.subscribe(ids)", "        self._legs[spread.spread_id] = self._prices.subscribe([])", PY),
 (P, "        self._prices.on_derived(as_tick(spread, now))", "        self._prices.on_tick(as_tick(spread, now))", PY),
 (P, "            self._prices.on_derived(Tick(PREFIX + spread_id, 0))", "", PY),
 (P, "        elif self._prices.is_watched(spread.instrument_id) or spread_id in self._legs:", "        elif False:", PY),
 (P, "        if old is not None:\n            old.close()", "        if False:\n            old.close()", PY),
 (D, "        for subscription in self._watchers.get(tick.instrument_id, ()):\n            subscription._offer(tick)\n\n    def tap", "\n    def tap", PY),
 # the server
 (A, "        if instrument is None and (spread := book.visible(user, instrument_id)) is not None:", "        if instrument is None and (spread := book.of_instrument(instrument_id)) is not None:", PY),
 (A, "        spread_visible=lambda user, i: book.visible(user, i) is not None,", "        spread_visible=lambda user, i: book.of_instrument(i) is not None,", PY),
 (A, "        await book.save(user.id)\n        return _spread_out(made)", "        return _spread_out(made)", PY),
 (A, "            raise HTTPException(404 if exc.missing else 422, str(exc)) from None", "            raise HTTPException(422, str(exc)) from None", PY),
 (W, "                 or self._spread_visible(connection.user, i)]  # fmt: skip", "                 ]  # fmt: skip", PY),
 (U, "            spreads.unlink()", "", PY),
 # the form
 (F, "    if (change.ratio !== undefined && change.multiplier === undefined && l.multiplier === l.ratio) next.multiplier = change.ratio;", "", UI),
 (F, "  return { ...d, legs: d.legs.map((l, i) => (i === index ? { ...l, quote: !turnOff } : l)) };", "  return { ...d, legs: d.legs.map((l, i) => (i === index ? { ...l, quote: true } : l)) };", UI),
 (F, "  if (!legs.some((l) => l.quote)) legs[0] = { ...legs[0]!, quote: true };", "", UI),
 (F, "  if (d.legs.length <= MIN_LEGS) return d;", "", UI),
 (F, "  if (d.legs.length >= MAX_LEGS) return d;", "", UI),
 (F, "  return d.legs.flatMap((l, i) => (l.instrumentId ? [] : [`leg ${i + 1}: pick its contract`]));", "  return [];", UI),
 (F, "    name: d.name.trim(),", "    name: d.name,", UI),
 (F, "  return legs.map((l) => `${l.ratio > 0 ? \"+\" : \"−\"}${Math.abs(l.ratio)} ${name(l.instrument_id)}`).join(\" \");", "  return legs.map((l) => `${Math.abs(l.ratio)} ${name(l.instrument_id)}`).join(\" \");", UI),
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
