"""#207 mutation check: break one protection rule, the basket tests must notice."""
import subprocess
M = [
 ("src/laddrix/risk/checks.py", 'elif getattr(o, "planned", False):', "elif True:"),
 ("src/laddrix/risk/checks.py", 'elif getattr(o, "planned", False):', "elif False:"),
 ("src/laddrix/risk/checks.py", "return longs >= shorts\n", "return shorts == 0 or longs > 0\n"),
 ("src/laddrix/risk/checks.py", "            net[i] = net.get(i, 0) - o.leaves_qty\n", "            pass\n"),
 ("src/laddrix/risk/checks.py", "self._instrument_problem(instrument, replace(new, qty=leaves), others)\n                if grows", "None\n                if grows"),
 ("src/laddrix/orders/basket.py", "if not stopped and buys and rest:", "if False:"),
 ("src/laddrix/orders/basket.py", "if all(v is not None and v.status is OrderStatus.FILLED for v in views):", "if all(v is not None for v in views):"),
 ("src/laddrix/orders/basket.py", "                    await self._orders.cancel(i)\n", "                    pass\n"),
 ("src/laddrix/orders/basket.py", "                if failed is not None:\n", "                if False:\n"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, a
    open(f, "w").write(src.replace(a, b, 1))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_basket.py", "tests/test_risk.py"], capture_output=True)
    if r.returncode: caught += 1
    else: print("SURVIVED:", f, a[:70])
    open(f, "w").write(src)
print(f"{caught}/{len(M)} caught")
