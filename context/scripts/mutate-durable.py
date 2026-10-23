"""#265 mutation check: break one rule of "on disk before the broker" (an order and a change
are written first; a failed or stalled disk refuses new orders but not forced closes or
cancels; a change is written with the larger size; bounded memory; the platform wires it);
the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

M = "src/mocktrade/orders/manager.py"
E = "src/mocktrade/store/events.py"
P = "src/mocktrade/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_durable_orders.py",
         "tests/test_order_manager.py", "tests/test_event_log.py"]
MUTANTS = [
 (M, "        if (problem := await self._make_durable(None)) is not None:", "        if (problem := None) is not None:"),
 (M, "            if not request.forced_close:  # a forced close only reduces: it still goes", "            if True:"),
 (M, "        if (problem := await self._make_durable(sending)) is not None:", "        if (problem := None) is not None:"),
 (M, "        worst = replace(new, qty=max(order.request.qty, new.qty))", "        worst = new"),
 (M, "            if order.pending is Pending.MODIFY:\n                order.pending = Pending.NONE\n            raise OrderError(f\"not sent", "            if False:\n                order.pending = Pending.NONE\n            raise OrderError(f\"not sent"),
 (E, "        if not self.healthy:\n            return f\"the audit log can't write", "        if False:\n            return f\"the audit log can't write"),
 (E, "        if self.backlog >= self.max_backlog // 2:", "        if False:"),
 (E, "            await asyncio.wait_for(self.flush(), timeout_s)", "            await self.flush()"),
 (E, "        if not critical and self._queue.qsize() >= self.max_backlog:", "        if False:"),
 (E, "        if event is not None:\n            self.record(event)", "        if False:\n            self.record(event)"),
 (E, "        self._put((stamp, account_id, kind, \"\", detail, json.dumps(payload)), critical=False)", "        self._put((stamp, account_id, kind, \"\", detail, json.dumps(payload)))"),
 (P, "                self.event_log.persist\n                if self.event_log is not None and self.durable_orders", "                None\n                if self.event_log is not None and self.durable_orders"),
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
