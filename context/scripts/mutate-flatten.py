"""#262 mutation check: break one rule of STOP / cancel / flatten (a kill switch lets through
only a forced close that is also reduce-only; the desk marks its closes; an unanswered order
stays unconfirmed when cancelled; flatten never touches a position of unknown size, runs one
at a time, says what it did; the STOP report); the tests must notice. Restores each file in
`finally`. Run alone."""
import subprocess

C = "src/laddrix/risk/checks.py"
D = "src/laddrix/risk/closing.py"
M = "src/laddrix/orders/manager.py"
F = "src/laddrix/risk/flatten.py"
P = "src/laddrix/platform.py"
ROUTES_RISK = "src/laddrix/server/routes/risk.py"
O = "src/laddrix/orders/codec.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_flatten.py", "tests/test_exits.py",
         "tests/test_server.py", "tests/test_unconfirmed.py", "tests/test_risk_checks.py",
         "tests/test_order_manager.py"]
MUTANTS = [
 (C, "        if request is not None and request.forced_close and request.reduce_only:", "        if request is not None and request.forced_close:"),
 (C, "        if request is not None and request.forced_close and request.reduce_only:", "        if request is not None and request.reduce_only:"),
 (C, "            self._kill_problem(request)\n            or self._off_problem(request, request.qty, others)", "            self._kill_problem()\n            or self._off_problem(request, request.qty, others)"),
 (D, "                               forced_close=True)", "                               forced_close=False)"),
 (O, '        forced_close=bool(d.get("forced_close", False)),', '        forced_close=False,'),
 (M, "        if order.pending is Pending.UNCONFIRMED:\n            # its fate is unknown", "        if False:\n            # its fate is unknown"),
 (F, "                if unknown is not None or instrument is None:", "                if instrument is None:"),
 (F, "        async with self._lock:", "        if True:"),
 (F, "                if product is not None and p.product is not product:", "                if False:"),
 (F, "                if not p.qty or (instrument_id and p.instrument_id != instrument_id):", "                if not p.qty:"),
 (F, '            "can_flatten": any(r["unknown"] is None for r in rows),', '            "can_flatten": bool(rows),'),
 (F, "        unconfirmed = [v.client_order_id for v in before if v.pending is Pending.UNCONFIRMED]", "        unconfirmed = []"),
 (F, "        failed = dict(failures)", "        failed = {}"),
 (F, "        self.last_stop = report", "        pass"),
 (F, "            self.last = result", "            pass"),
 (P, "            if m.instrument_id == instrument_id and m.product in (None, product):", "            if False:"),
 (P, "            if i == instrument_id and p in (None, product):", "            if False:"),
 (ROUTES_RISK, "        if not body.confirm:\n            raise HTTPException(422, \"flatten needs", "        if False:\n            raise HTTPException(422, \"flatten needs"),
 (ROUTES_RISK, "        reports = _stop_reports([running], before, failures, user.id, body.reason)", "        reports = []"),
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
