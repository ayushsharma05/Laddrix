"""#217 mutation check: break one MTM / square-off rule, tests/test_mtm.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files). Anchor each mutant so its text is
unique: a shorter line can hide inside a more indented one earlier in the file."""
import subprocess
T, S, C = "src/mocktrade/risk/mtm.py", "src/mocktrade/risk/squareoff.py", "src/mocktrade/risk/closing.py"
G, K = "src/mocktrade/ops/guards.py", "src/mocktrade/risk/checks.py"
M = [
 (T, "    if rule.max_loss_paise is not None and pnl <= -rule.max_loss_paise:\n        return (", "    if rule.max_loss_paise is not None and pnl < -rule.max_loss_paise - 1000:\n        return ("),
 (T, "        if value and -pnl * 100 >= rule.max_loss_pct * value:", "        if value and -pnl >= rule.max_loss_pct * value:"),
 (T, "            if self._fired.get(rule.key) == today:\n                continue", "            if False:\n                continue"),
 (T, "        closing = close and any(v.qty for v in views)", "        closing = any(v.qty for v in views)"),
 (T, "                open_ = [v for v in self._book.positions() if v.qty]", "                open_ = [v for v in self._book.positions() if v.qty][:1]"),
 (T, "        self._fired.pop(rule.key, None)  # a new limit is watched afresh", "        pass"),
 (T, "        self._save()", "        pass"),
 (T, '    if rule.max_loss_paise is not None and pnl <= -rule.max_loss_paise:\n        return (', '    if False:\n        return ('),
 (T, "        if rule.max_loss_paise is None and rule.max_loss_pct is None:", "        if False:"),
 (S, "            if p.product is not Product.MIS or not p.qty:", "            if not p.qty:"),
 (S, "            \"window_open\": self.warn_from <= now.time() < max(self.cutoffs.values()),", "            \"window_open\": True,"),
 (S, "        for p in [p for p in self._book.positions() if p.product is Product.MIS and p.qty]:", "        for p in [p for p in self._book.positions() if p.qty]:"),
 (S, "    \"NFO\": time(15, 25)", "    \"NFO\": time(15, 20)"),
 (C, "    side = Side.SELL if qty > 0 else Side.BUY", "    side = Side.BUY"),
 (C, "    piece = max(instrument.lot_size, max_lots * instrument.lot_size)", "    piece = abs(qty)"),
 (K, "            if request.basket_id and order.request.basket_id == request.basket_id:\n                continue", "            if False:\n                continue"),
 (G, "            if flat:", "            if False:"),
 (G, '                self._alerts.raise_(key, "WARN", text, account_id=account_id)', '                pass'),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    if a not in src:
        print("MISSING:", f, a[:60]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_mtm.py"],
                           capture_output=True, timeout=120)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
