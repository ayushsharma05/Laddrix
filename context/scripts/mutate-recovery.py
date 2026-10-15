"""#206 mutation check: break one recovery rule, the tests must notice."""
import subprocess
F = "src/mocktrade/recovery.py"
M = [
 (F, "        if self.state == \"ok\":\n            return None", "        if True:\n            return None"),
 (F, "            report.fills += 1", "            report.fills += 1\n            book.replay(e.client_order_id, e.order.request, e.order.filled_qty + 75, 75, e.fill.price_paise)"),
 (F, "    orders.reconcile(broker_orders)\n", "\n"),
 (F, "            report.closed.append(view.client_order_id)", "            report.closed.append(view.client_order_id)\n            continue\n        if True:\n            pass"),
 (F, "            report.problems.append(\n                f\"order {view", "            print(\n                f\"order {view"),
 (F, "        if a != b:", "        if False:"),
 (F, "            if e.ts < since and e.order", "            if e.order"),
 (F, "                price = close(p.instrument_id) or round(p.avg_price_paise)", "                price = round(p.avg_price_paise)"),
 (F, "            book.mark(tick)", "            pass"),
 (F, "            gate.set(\"failed\", report.summary())", "            gate.set(\"ok\", report.summary())"),
 ("src/mocktrade/platform.py", "        if runtime.recovery is not None:\n            await runtime.recovery.finished.wait()\n        await runtime.algos.cancel_orphans()", "        await runtime.algos.cancel_orphans()"),
 ("src/mocktrade/tools/serve.py", "            recover_on_start=True,", "            recover_on_start=False,"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, a
    open(f, "w").write(src.replace(a, b, 1))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_recovery.py", "tests/test_serve.py"], capture_output=True)
    if r.returncode: caught += 1
    else: print("SURVIVED:", f, a[:70])
    open(f, "w").write(src)
print(f"{caught}/{len(M)} caught")
