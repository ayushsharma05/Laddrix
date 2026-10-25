"""#261 mutation check: break one rule of coordinated exits (closing orders marked reduce-only;
the risk gate refusing an over-close; the exit desk's lock, algo stop, cancels, wait and
what it leaves to orders already closing; stop algos capping their exit); the tests must
notice. Restores each file in `finally`. Run alone."""
import subprocess

C = "src/mocktrade/risk/checks.py"
D = "src/mocktrade/risk/closing.py"
E = "src/mocktrade/algos/engine.py"
P = "src/mocktrade/algos/protect.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_exits.py", "tests/test_mtm.py",
         "tests/test_protect.py", "tests/test_risk_checks.py", "tests/test_algo_engine.py"]
MUTANTS = [
 (C, "        if not request.reduce_only:\n            return None", "        if True:\n            return None"),
 (C, "        closable = held if request.side is Side.SELL else -held", "        closable = abs(held)"),
 (C, "            if o.request.reduce_only\n            and o.request.instrument_id", "            if o.request.instrument_id"),
 (C, "            and o.request.product is request.product\n            and o.request.side is request.side\n        )", "            and o.request.side is request.side\n        )"),
 (C, "        if qty > closable - taken:", "        if qty > closable:"),
 (C, "            or (self._reduce_only_problem(new, leaves, others) if grows else None)", "            or None"),
 (D, '                               basket_id=basket_id, reduce_only=True,\n                               forced_close=True)', '                               basket_id=basket_id,\n                               forced_close=True)'),
 (D, "        async with lock:\n            return await self._close(", "        if True:\n            return await self._close("),
 (D, "            result.stopped_algos = engine.stop_on(", "            result.stopped_algos = [] or engine.algos() and []\n            (lambda *a: None)("),
 (D, "            if view.pending is Pending.UNCONFIRMED:\n                continue", "            if False:\n                continue"),
 (D, "                await self._orders.cancel(view.client_order_id)\n", "                pass\n"),
 (D, "            await self._sleep(POLL_S)\n            waited += POLL_S", "            break"),
 (D, "        held = self._position(instrument.id, product)  # fills meanwhile are in it", "        pass"),
 (D, "        result.left_open = min(abs(held), working)", "        result.left_open = 0"),
 (D, "            and v.request.side is side and v.leaves_qty > 0", "            and v.leaves_qty > 0"),
 (D, "            if v.request.instrument_id == instrument_id and v.request.product is product\n", "            if v.request.instrument_id == instrument_id\n"),
 (E, "            and p.instrument_id == instrument_id and p.product is product", "            and p.instrument_id == instrument_id"),
 (E, "        reduces = held if exit_side is Side.SELL else -held", "        reduces = abs(held)"),
 (E, "            if o.request.reduce_only and o.client_order_id not in mine", "            if o.request.reduce_only"),
 (E, "            parent_id=p.algo_id,\n            reduce_only=reduce_only,", "            parent_id=p.algo_id,"),
 (P, "order_type=kind, reduce_only=True, **kw)", "order_type=kind, **kw)"),
 (P, "        if closable is None:", "        if True:"),
 (P, "        if closable <= 0:\n            ctx.finish(", "        if closable < 0:\n            ctx.finish("),
 (P, "        return min(qty, closable)", "        return qty"),
 (P, "        if ctx.send(ctx.qty, self.target, reduce_only=True):", "        if ctx.send(ctx.qty, self.target):"),
 (P, "check_remaining=False,\n                            reduce_only=True):", "check_remaining=False,\n                            reduce_only=False):"),
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
