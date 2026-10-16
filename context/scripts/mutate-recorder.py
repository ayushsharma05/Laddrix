"""#223 mutation check: break one tick-recording rule, tests/test_ticks_recorder.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files). Anchor each mutant so its text is
unique: a shorter line can hide inside a more indented one earlier in the file.
Not listed: float instead of integer microseconds in `_us` (no counterexample at today's
timestamps, checked over a whole second in steps of 7 µs; the exact form costs nothing)."""
import subprocess
T, M_ = "src/mocktrade/research/ticks.py", "src/mocktrade/marketdata/service.py"
M = [
 (T, "        tick.high_paise, tick.low_paise, tick.close_paise, tick.open_interest,", "        tick.high_paise, tick.low_paise, tick.close_paise, None,"),
 (T, "        [list(level) for level in tick.bids], [list(level) for level in tick.asks],", "        [list(level) for level in tick.bids], [],"),
 (T, "            day = recorded_at.astimezone(IST).date()", "            day = recorded_at.astimezone(UTC).date()"),
 (T, "            with open(folder / f\"{segment}{SUFFIX}\", \"ab\") as f:", "            with open(folder / f\"{segment}{SUFFIX}\", \"wb\") as f:"),
 (T, "        for _, _, tick in heapq.merge(*streams):", "        for _, _, tick in sorted(x for s in streams for x in s)[::-1]:"),
 (T, "                    if wanted is None or tick.instrument_id in wanted:", "                    if True:"),
 (T, "        except (EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:", "        except ZeroDivisionError as exc:"),
 (T, "        gone = days[: len(days) - self.keep_days]", "        gone = days[self.keep_days :]"),
 (T, "        if self.keep_days <= 0 or len(days) <= self.keep_days:", "        if len(days) <= self.keep_days:"),
 (T, "            index[instrument_id] = index.get(instrument_id, 0) + n", "            index[instrument_id] = n"),
 (T, "        if self._record(tick.instrument_id):", "        if True:"),
 (T, "            self._buffer = batch + self._buffer  # kept; tried again with the next flush", "            pass"),
 (T, "            await asyncio.shield(self.flush())", "            pass"),
 (M_, "        for tap in self._taps:\n            tap(tick)", "        for tap in self._taps[:0]:\n            tap(tick)"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    if a not in src:
        print("MISSING:", f, a[:60]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_ticks_recorder.py"],
                           capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
