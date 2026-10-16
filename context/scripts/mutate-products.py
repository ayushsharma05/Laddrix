"""#258 mutation check: break one rule that keeps positions apart by product (comparing with the
broker, the restart rebuild, the paper broker's rows, what an order can close, the screen's
alerts); the Python or UI tests must notice. Restores each file in `finally`; a hang counts as
caught. Run alone. Every anchor is checked to be unique first."""
import subprocess

RC = "src/mocktrade/positions/reconcile.py"
RV = "src/mocktrade/recovery.py"
PB = "src/mocktrade/broker/paper.py"
CK = "src/mocktrade/risk/checks.py"
PF = "src/mocktrade/platform.py"
BK = "src/mocktrade/positions/book.py"
ST = "ui/src/state/store.ts"
PO = "ui/src/widgets/books/Positions.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_reconcile_positions.py", "tests/test_recovery.py",
       "tests/test_risk_checks.py", "tests/test_admin.py", "tests/test_paper_broker.py"], ".")
UI = (["npx", "vitest", "run", "src/state", "src/widgets/books"], "ui")
MUTANTS = [
 (RC, "        broker[key] = broker.get(key, 0) + p.qty  # never overwritten: rows add up", "        broker[key] = p.qty"),
 (RC, "        if p.product is None:\n            unsure.add(p.instrument_id)", "        if False:\n            unsure.add(p.instrument_id)"),
 (RC, "        a = sum(q for (i, _), q in ours.items() if i == instrument_id)", "        a = 0"),
 (RC, "        if a != b:\n            out[(instrument_id, None)] = (a, b)", "        if a == b:\n            out[(instrument_id, None)] = (a, b)"),
 (RC, "    keys = {k for k in ours if k[0] not in unsure} | {k for k in broker if k[0] not in unsure}", "    keys = {k for k in broker if k[0] not in unsure}"),
 (RC, "        self._alerts[mismatch.key] = mismatch", "        self._alerts[(mismatch.instrument_id, None)] = mismatch"),
 (RC, "                self._raise(Mismatch(instrument_id, our_qty, broker_qty, since, product))", "                self._raise(Mismatch(instrument_id, our_qty, broker_qty, since))"),
 (RC, "        what = f\"{self.instrument_id} {self.product.value}\" if self.product else self.instrument_id", "        what = self.instrument_id"),
 (RV, "    different = differences(book.qty_by_product(), broker_positions)", "    different = differences({(i, None): q for i, q in book.net_by_instrument().items()}, broker_positions)"),
 (PB, "round(p.avg_price_paise) if p.qty else 0, product=p.product", "round(p.avg_price_paise) if p.qty else 0, product=None"),
 (CK, "        now = self._product_position(request.instrument_id, request.product)", "        now = self._position(request.instrument_id)"),
 (CK, "            and o.request.product is request.product", "            and True"),
 (PF, "            product_position=book.qty,\n", "\n"),
 (PF, "            book.qty_by_product, broker, account_id=account.id, clock=clock", "            lambda: {(i, None): q for i, q in book.net_by_instrument().items()}, broker, account_id=account.id, clock=clock"),
 (BK, "        return position.qty if position else 0", "        return self.net_qty(instrument_id)"),
 (ST, "  return `${a.instrument_id}|${a.product ?? \"\"}`;", "  return `${a.instrument_id}|`;"),
 (ST, "  return alerts[positionKey(p)] ?? alerts[`${p.instrument_id}|`];", "  return alerts[positionKey(p)];"),
 (ST, "          delete alerts[alertKey(m)];", "          delete alerts[`${m.instrument_id}|`];"),
 (PO, "            const alert = alertFor(alerts, p);", "            const alert = alerts[`${p.instrument_id}|NRML`] ?? Object.values(alerts).find((a) => a.instrumentId === p.instrument_id);"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    cmd, cwd = UI if f.startswith("ui/") else PY
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
