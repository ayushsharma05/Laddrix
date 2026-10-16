"""#225 mutation check: break one backtest rule, tests/test_backtest.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files). Anchor each mutant so its text is
unique: a shorter line can hide inside a more indented one earlier in the file."""
import subprocess
B = "src/mocktrade/research/backtest.py"
M = [
 (B, "        if not state[\"started\"] and now >= start_at:", "        if not state[\"started\"]:"),
 (B, "        while state[\"next_beat\"] <= now:\n            rt.algos.heartbeat()", "        if False:\n            rt.algos.heartbeat()"),
 (B, "            if broker.idle and not rt.algos.busy:\n                return", "            if True:\n                return"),
 (B, "        path = [o, low, h, c] if c >= o else [o, h, low, c]", "        path = [o, h, low, c]"),
 (B, "                        bids=(DepthLevel(price - t, 10**9, 1),),", "                        bids=(DepthLevel(price, 10**9, 1),),"),
 (B, "        worst = min(worst, equity - peak)", "        worst = min(worst, equity)"),
 (B, "    winning = sum(1 for d in ran if d[\"totals\"][\"net_paise\"] > 0)", "    winning = sum(1 for d in ran if d[\"totals\"][\"gross_paise\"] > 0)"),
 (B, "                if run.state in (\"queued\", \"running\"):  # the server stopped mid-run", "                if False:  # the server stopped mid-run"),
 (B, "            if not (low <= min(o, c) and max(o, c) <= h and low > 0):", "            if False:"),
 (B, "        if candles and self._candles is None:", "        if False:"),
 (B, "        if state[\"error\"]:\n            raise BacktestError", "        if False:\n            raise BacktestError"),
 (B, "        return iter(candles_to_ticks(instrument, rows)) if rows else None", "        return self._ticks(day)"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    if a not in src:
        print("MISSING:", f, a[:60]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_backtest.py"],
                           capture_output=True, timeout=120)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
