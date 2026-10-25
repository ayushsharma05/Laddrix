"""K9.1 mutation check: break one line of an execution algo, the tests must notice."""
import subprocess
M = [
 ("src/laddrix/algos/iceberg.py", "if ctx.working() or ctx.sending or ctx.held:", "if ctx.working() or ctx.held:"),
 ("src/laddrix/algos/iceberg.py", "return max(1, min(self.display + swing, remaining_lots))", "return max(1, self.display + swing)"),
 ("src/laddrix/algos/iceberg.py", "if child.status is OrderStatus.FILLED:", "if child.status.is_final:"),
 ("src/laddrix/algos/execution.py", "return price > limit if side is Side.BUY else price < limit", "return price > limit"),
 ("src/laddrix/algos/execution.py", "if done >= MODIFY_BUDGET:", "if False:"),
 ("src/laddrix/algos/schedule.py", "due = math.ceil(total * frac - 1e-9)", "due = math.floor(total * frac)"),
 ("src/laddrix/algos/schedule.py", "urgent = (due - filled) > self.max_behind * total", "urgent = False"),
 ("src/laddrix/algos/schedule.py", "step = self.interval * (1 + self.rng.uniform(-JITTER, JITTER))", "step = self.interval"),
 ("src/laddrix/algos/schedule.py", "            self.end_at = close\n", "            pass\n"),
 ("src/laddrix/algos/schedule.py", "if not OPEN <= local.time() < CLOSE or local.weekday() >= 5:", "if not OPEN <= local.time() < CLOSE:"),
 ("src/laddrix/algos/schedule.py", "if self.window_weight <= 0:\n            return super().fraction(now)", "if False:\n            return super().fraction(now)"),
 ("src/laddrix/algos/schedule.py", "sign = 1 if ctx.side is Side.BUY else -1", "sign = 1"),
 ("src/laddrix/algos/schedule.py", "return round((tick.volume * tick.avg_price_paise - v0 * p0) / traded)", "return tick.avg_price_paise"),
 ("src/laddrix/algos/schedule.py", "return self.cum[whole] + (self.cum[whole + 1] - self.cum[whole]) * (minutes - whole)", "return self.cum[whole]"),
 ("src/laddrix/algos/pov.py", "size = min(due, self.max_lots) * lot", "size = due * lot"),
 ("src/laddrix/algos/pov.py", "due = lots(ctx, int(traded * self.share) - filled - working)", "due = lots(ctx, int(traded * self.share) - filled)"),
 ("src/laddrix/algos/pov.py", "return max(0, tick.volume - self.base)", "return tick.volume"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, a
    open(f, "w").write(src.replace(a, b, 1))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_exec_algos.py"], capture_output=True)
    if r.returncode: caught += 1
    else: print("SURVIVED:", f, a[:70])
    open(f, "w").write(src)
print(f"{caught}/{len(M)} caught")
