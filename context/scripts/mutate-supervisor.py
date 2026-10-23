"""#273 mutation check: break one rule of the supervisor and the data fence (restart a crashed
loop after a pause; quarantine after repeated failures; a critical loop down pauses its
account's new orders, not cancels, not other accounts; a side loop pauses nothing; ready()
needs the watchdog; the platform's loops are watched; one server per data folder, the lock
held for life and freed on close after the audit trail); the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

S = "src/mocktrade/ops/supervisor.py"
F = "src/mocktrade/ops/fence.py"
P = "src/mocktrade/platform.py"
V = "src/mocktrade/tools/serve.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_supervisor.py"]
MUTANTS = [
 (S, "                await self._sleep(wait)\n                self._alert(f\"loop:{name}\", \"OK\", f\"{name} restarted\", account_id)\n                continue", "                return"),
 (S, "                if len(loop.failures) >= QUARANTINE_AFTER:", "                if False:"),
 (S, "            if loop.critical and loop.down and loop.account_id in (None, account_id):", "            if loop.down:"),
 (S, "        return self._supervisor.paused(self._account_id)\n\n    async def admit_modify", "        return None\n\n    async def admit_modify"),
 (S, "        return None  # cancelling only ever reduces risk", "        return self._supervisor.paused(self._account_id)"),
 (S, "        if self._beat is None or self._now() - self._beat > STALE_AFTER_S:", "        if self._beat is None:"),
 (F, "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "            pass"),
 (P, "        pause = PauseGate(self.supervisor, account.id)  # a critical loop down (#273)\n        checks = (pause,", "        checks = ("),
 (P, "            keep(f\"{a}: broker updates\", runtime.orders.run, account_id=a),", "            runtime.orders.run(),"),
 (V, "        fence.acquire()  # one trading server per data folder (#273)", "        pass"),
 (V, "        if self.fence is not None:\n            self.fence.release()", "        if False:\n            self.fence.release()"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
