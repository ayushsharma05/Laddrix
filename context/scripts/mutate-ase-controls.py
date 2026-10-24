"""#425 mutation check: break one rule of the Autospreader's controls and actions (the
re-quote threshold and the move made at once, max unhedged, the chase and its cap, the
timeout and its one ALERT, hedge-now, flatten and its wait for the cancels, the preview
check, hedges kept while paused, the controls in the definition and the API, the screens);
the tests must notice. Restores each file in `finally`; a hang counts as caught. Run alone
from the repo root."""
import subprocess

A = "src/mocktrade/algos/autospreader.py"
E = "src/mocktrade/algos/engine.py"
M = "src/mocktrade/spreads/model.py"
S = "src/mocktrade/spreads/store.py"
P = "src/mocktrade/server/app.py"
F = "src/mocktrade/server/formats.py"
AM = "ui/src/widgets/ase/model.ts"
AW = "ui/src/widgets/ase/Autospreader.tsx"
AG = "ui/src/widgets/algos/AlgoManager.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_ase_controls.py", "tests/test_autospreader.py",
       "tests/test_spread_api.py", "tests/test_spreads.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/ase", "src/widgets/algos"], "ui")
MUTANTS = [
 # the re-quote threshold, max unhedged
 (A, "            if not worse and abs(price - current) < self.controls.requote_ticks * tick:", "            if abs(price - current) < self.controls.requote_ticks * tick:", PY),
 (A, "            if not worse and abs(price - current) < self.controls.requote_ticks * tick:", "            if False:", PY),
 (A, "            worse = current > price if self._down() else current < price", "            worse = current < price if self._down() else current > price", PY),
 (A, "        if self.controls.max_unhedged_lots:\n            caps.append", "        if False:\n            caps.append", PY),
 # the chase
 (A, "                    target = max(level.price_paise - step, first - reach)", "                    target = level.price_paise - step", PY),
 (A, "                    moves = target < r.price_paise", "                    moves = target != r.price_paise", PY),
 (A, "                reach = tick * self.controls.chase_ticks", "                reach = tick * (self.controls.chase_ticks + 1)", PY),
 # the timeout
 (A, "        if waited < self.controls.hedge_timeout_s:", "        if waited <= 0:", PY),
 (A, "        if not self.alerted:\n            self.alerted = True", "        if True:\n            self.alerted = True", PY),
 (A, "        self._hedge(ctx)  # a hedge that couldn't go (no price yet) is tried again\n        self._chase(ctx)\n        self._timeout(ctx)", "        self._hedge(ctx)  # a hedge that couldn't go (no price yet) is tried again\n        self._chase(ctx)", PY),
 # hedge now
 (A, "            rest = need - self._covering(ctx, leg, side)", "            rest = need", PY),
 (A, "                if act and c.pending is Pending.NONE:\n                    ctx.modify", "                if False:\n                    ctx.modify", PY),
 (A, "        return protected_limit(ctx.instrument(leg.instrument_id), side, level.price_paise, AUTO)", "        return level.price_paise", PY),
 # flatten
 (A, "            return  # a cancel hasn't landed yet", "            pass  # a cancel hasn't landed yet", PY),
 (A, "            extra = self._net(ctx, leg) - complete * abs(leg.ratio) * lots[leg.instrument_id]", "            extra = self._net(ctx, leg)", PY),
 (A, "        return ctx.filled(leg.instrument_id, side) - ctx.filled(\n            leg.instrument_id, _other_side(side)\n        )", "        return ctx.filled(leg.instrument_id, side)", PY),
 (A, "        if not excess and not self._short(ctx):\n            return ActionPreview(FLATTEN", "        if False:\n            return ActionPreview(FLATTEN", PY),
 (A, "        return not (r.instrument_id == self.q.instrument_id and r.side is ctx.side)", "        return r.instrument_id != self.q.instrument_id", PY),
 # the engine
 (E, "        if list(now.lines) != list(expect):", "        if False:", PY),
 (E, "        if not now.ok:\n            raise AlgoError(now.why)", "        if False:\n            raise AlgoError(now.why)", PY),
 (E, "            if p.state is not AlgoState.WORKING and not self._keeps(p, order):", "            if p.state is not AlgoState.WORKING:", PY),
 (E, "            elif p.state in (AlgoState.PAUSED, AlgoState.STOPPING):\n                watch", "            elif p.state is AlgoState.STOPPING:\n                watch", PY),
 (F, "        \"actions\": [{\"name\": x.name, \"label\": x.label, \"help\": x.help} for x in a.actions],\n", "", PY),
 # the definition and the API
 (M, "    controls, problems = controls_from_dict(d.get(\"controls\"))", "    controls, problems = Controls(), []", PY),
 (S, "                           old.created_at, self._clock(), controls or old.controls)  # fmt: skip", "                           old.created_at, self._clock(), controls or Controls())  # fmt: skip", PY),
 (P, "            raise HTTPException(422, \"; \".join(problems))", "            return None", PY),
 # the screens
 (AM, "  if (typeof typed === \"string\") return typed;", "  if (typed) return String(typed);", UI),
 (AM, "    if (!parsed.ok) problems.push(parsed.problem);", "    if (!parsed.ok) controls[p.name] = null;", UI),
 (AW, "    const first = [...missing(draft), ...tuned.problems];", "    const first = missing(draft);", UI),
 (AG, "      .post<AlgoView>(actionUrl(algoId, preview.name), { expect: preview.lines })", "      .post<AlgoView>(actionUrl(algoId, preview.name), { expect: [] })", UI),
 (AG, "          {asked.preview.ok && (\n            <button type=\"button\" className=\"btn small danger\" onClick={confirm}>", "          {(\n            <button type=\"button\" className=\"btn small danger\" onClick={confirm}>", UI),
 (AG, "        {current &&\n          canStop(current) &&", "        {current &&", UI),
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
