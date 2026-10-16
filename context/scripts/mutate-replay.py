"""#224 mutation check: break one replay rule, tests/test_replay.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files). Anchor each mutant so its text is
unique: a shorter line can hide inside a more indented one earlier in the file."""
import subprocess
R, P = "src/mocktrade/research/replay.py", "src/mocktrade/broker/paper.py"
M = [
 (R, "                    await self._sleep(min(gap / self.speed, MAX_WAIT_S))", "                    await self._sleep(min(gap, MAX_WAIT_S))"),
 (R, "                    await self._sleep(min(gap / self.speed, MAX_WAIT_S))", "                    await self._sleep(gap / self.speed)"),
 (R, "                    for _ in range(TURNS):  # let acceptances and fills run between ticks", "                    for _ in range(1):  # let acceptances and fills run between ticks"),
 (R, "            await self._running.wait()  # paused while waiting: stop before this tick, not after", "            pass"),
 (R, "            self.clock.now = at\n", "            pass\n"),
 (R, "            seeking = self._seek_to is not None and at <= self._seek_to", "            seeking = self._seek_to is not None and at < self._seek_to"),
 (R, "        if to <= self.clock.now:", "        if False:"),
 (R, "        if speed not in SPEEDS:", "        if False:"),
 (R, "        self._seek_to = None  # a jump past the end ends with the day", "        pass"),
 (R, "        if self.state == \"playing\":\n            self._running.clear()", "        if True:\n            pass"),
 (P, "        if every_tick:\n            market_data.tap(self.on_tick)", "        if False:\n            market_data.tap(self.on_tick)"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    if a not in src:
        print("MISSING:", f, a[:60]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_replay.py"],
                           capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
