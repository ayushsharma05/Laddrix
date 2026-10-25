"""#328 mutation check: break one rule of the unprotected-position band (which events flag, what
counts as protected again, waiting for recovery, the restart list, the band's words, clearing);
the tests must notice. Restores each file in `finally`; a hang counts as caught. Run alone."""
import subprocess

G = "src/laddrix/ops/protection.py"
P = "src/laddrix/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_protection_guard.py",
         "tests/test_protect.py"]
MUTANTS = [
 (G, 'LOST_KINDS = ("PAUSED", "STOPPED", "FAILED", "DONE")', 'LOST_KINDS = ("STOPPED", "FAILED", "DONE")'),
 (G, 'LOST_KINDS = ("PAUSED", "STOPPED", "FAILED", "DONE")', 'LOST_KINDS = ("PAUSED", "STOPPED", "DONE")'),
 (G, 'LOST_KINDS = ("PAUSED", "STOPPED", "FAILED", "DONE")', 'LOST_KINDS = ("PAUSED", "STOPPED", "FAILED")'),
 (G, 'COVERED_KINDS = ("STARTED", "RESUMED")', 'COVERED_KINDS = ("STARTED",)'),
 (G, "STOPS = (OrderType.SL, OrderType.SL_M)", "STOPS = (OrderType.SL,)"),
 (G, "        if not self._is_protection(account_id, algo.code):\n            return\n", ""),
 (G, '            self._alerts.clear(key(*where), "protected again, or the position is closed")', '            pass'),
 (G, "        return any(s.code == code and s.family == PROTECTION for s in runtime.algos.specs())", "        return any(s.code == code for s in runtime.algos.specs())"),
 (G, "                and algo.state is AlgoState.WORKING\n", "\n"),
 (G, "                algo.instrument_id == instrument_id\n                and algo.state", "                algo.state"),
 (G, "                and r.side is closing\n", "\n"),
 (G, "                and r.order_type in STOPS\n", "\n"),
 (G, "                order.is_working\n                and r.instrument_id", "                r.instrument_id"),
 (G, "                and r.instrument_id == instrument_id\n                and r.side", "                and r.side"),
 (G, "                if gtt.request.instrument_id == instrument_id and not gtt.status.is_final:", "                if not gtt.status.is_final:"),
 (G, "                if gtt.request.instrument_id == instrument_id and not gtt.status.is_final:", "                if gtt.request.instrument_id == instrument_id:"),
 (G, "            if runtime.recovery is not None and not runtime.recovery.finished.is_set():\n                continue", "            pass"),
 (G, '            if open_qty == 0:\n                self._clear(where, "the position is closed")', '            if False:\n                self._clear(where, "the position is closed")'),
 (G, '            side = "long" if open_qty > 0 else "short"', '            side = "long"'),
 (G, '        closing = Side.SELL if open_qty > 0 else Side.BUY', '        closing = Side.SELL'),
 (G, '            if runtime is None:\n                self._clear(where, "the account is gone")\n                continue', '            if runtime is None:\n                continue'),
 (G, "                self._watched.add(account_id)\n                runtime.algos.subscribe", "                runtime.algos.subscribe"),
 (G, "    def check(self) -> None:\n        self._watch()\n", "    def check(self) -> None:\n"),
 (G, "            self._alerts.clear(key(*where), \"protected again, or the position is closed\")", "            pass"),
 (G, '            reason = event.detail or algo.reason', '            reason = event.detail'),
 (G, '        self.check()\n\n    def on_algo', '\n    def on_algo'),
 (P, 'lost.append(LostProtection(str(a.get("instrument_id", "")), words,', 'lost.append(LostProtection("", words,'),
]
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
    if survived: print("SURVIVED:", f, a[:90], "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
