"""#200 mutation check (equivalent mutants left out: the OCO wait for the target cancel is
also enforced by the engine's never-over-send rule): break one protection rule, tests/test_protect.py must notice."""
import subprocess
F = "src/laddrix/algos/protect.py"
M = [
 (F, "    return ltp <= stop if exit_side is Side.SELL else ltp >= stop", "    return ltp < stop if exit_side is Side.SELL else ltp > stop"),
 (F, "        if self.best is None or (ltp > self.best if selling else ltp < self.best):", "        if True:"),
 (F, "        return self.stop - step if exit_side is Side.SELL else self.stop + step", "        return self.stop"),
 (F, "            return int((price - distance) // tick * tick)", "            return int(price - distance)"),
 (F, "        if (self.points is None) == (self.pct is None):", "        if self.points is None and self.pct is None:"),
 (F, "            self._exit(ctx)  # a refused exit", "            pass  # a refused exit"),
 (F, "        if (self.target <= self.stop) if selling else (self.target >= self.stop):", "        if False:"),
 (F, "        if tick is not None and touched(ctx.side, tick.ltp_paise, self.stop):\n            return ctx.fail", "        if False:\n            return ctx.fail"),
 (F, "                qty = open_qty - covered", "                qty = open_qty"),
 (F, "        if others or exits or self.exit_sending:\n            return", "        if False:\n            return"),
 (F, "        return entered > 0 and entry_over and exited >= entered and not ctx.working()", "        return entered > 0 and exited >= entered"),
 (F, "        self.target = down(target) if up else upward(target)", "        self.target = target"),
 (F, "        if open_qty > 0:\n            return f\"UNPROTECTED", "        if False:\n            return f\"UNPROTECTED"),
 ("src/laddrix/algos/engine.py", "    return request.instrument_id == p.instrument_id and request.side is p.side", "    return request.instrument_id == p.instrument_id"),
 ("src/laddrix/platform.py", "                if e.extra.get(\"family\") == \"protection\":", "                if True:"),
 ("src/laddrix/platform.py", "            log.record_note(\"ALGO_LOST\"", "            (lambda *a: None)(\"ALGO_LOST\""),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, a
    open(f, "w").write(src.replace(a, b, 1))
    try:  # a mutant that hangs the tests counts as caught; the file is always restored
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_protect.py"],
                           capture_output=True, timeout=60)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:80])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
