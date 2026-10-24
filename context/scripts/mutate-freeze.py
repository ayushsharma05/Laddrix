"""#424 mutation check: break one rule of freeze quantities (reading the file, applying it to
futures and options now and after a refresh, clearing a removed one, the one-lot floor, the
order check, the whole-lot cap, the risk gate, forced closes, the order spec, the API, the
setting, the demo's sample, the ticket); the tests must notice. Restores each file in
`finally`; a hang counts as caught. Run alone from the repo root."""
import subprocess

FR = "src/mocktrade/refdata/freeze.py"
ST = "src/mocktrade/refdata/store.py"
IN = "src/mocktrade/domain/instruments.py"
OR = "src/mocktrade/domain/orders.py"
RG = "src/mocktrade/risk/checks.py"
CL = "src/mocktrade/risk/closing.py"
SP = "src/mocktrade/domain/order_spec.py"
AP = "src/mocktrade/server/app.py"
SV = "src/mocktrade/tools/serve.py"
TM = "ui/src/widgets/ticket/model.ts"
TT = "ui/src/widgets/ticket/Ticket.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_freeze.py", "tests/test_serve.py",
       "tests/test_spread_api.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/ticket"], "ui")
MUTANTS = [
 # the file
 (FR, "        if len(parts) != 2 or not parts[0]:", "        if not parts[0]:", PY),
 (FR, "        if not qty.isdigit() or int(qty) <= 0:", "        if not qty.isdigit():", PY),
 (FR, "        if underlying in found:", "        if False:", PY),
 (FR, "        underlying, qty = parts[0].upper(), parts[1]", "        underlying, qty = parts[0], parts[1]", PY),
 (FR, "        found, problems = {}, [f\"can't read it: {exc.strerror or exc}\"]", "        raise", PY),
 # the store
 (ST, "        if freeze and instrument.kind in _DERIVATIVES and instrument.freeze_qty is None:", "        if freeze and instrument.freeze_qty is None:", PY),
 (ST, "            if freeze >= instrument.lot_size:  # below one lot", "            if True:  # below one lot", PY),
 (ST, "        changed = set(self._freeze) | set(by_underlying)", "        changed = set(by_underlying)", PY),
 (ST, "                self.add(replace(instrument, freeze_qty=None))", "                self.add(instrument)", PY),
 (ST, "        \"freeze_qty\": instrument.freeze_qty,\n", "", PY),
 # the instrument and the order
 (IN, "        if self.freeze_qty is not None and qty > self.freeze_qty:", "        if self.freeze_qty is not None and qty >= self.freeze_qty:", PY),
 (IN, "        return self.freeze_qty - self.freeze_qty % self.lot_size", "        return self.freeze_qty", PY),
 (IN, "        if self.freeze_qty is not None and self.freeze_qty < self.lot_size:", "        if False:", PY),
 (OR, "        instrument.check_freeze(self.qty)\n", "", PY),
 # what uses it
 (RG, "        return qty if freeze is None else min(qty, freeze)", "        return qty", PY),
 (CL, "        piece = min(piece, freeze)", "        pass", PY),
 (SP, "        freeze_qty=instrument.max_order_qty,", "        freeze_qty=instrument.freeze_qty,", PY),
 (AP, "        \"freeze_qty\": i.freeze_qty,\n", "", PY),
 (SV, "                problems.append(f\"MOCKTRADE_FREEZE_FILE can't be read: {exc.strerror or exc}\")", "                pass", PY),
 (SV, "            freeze_file=Path(freeze_text) if freeze_text else None,", "            freeze_file=None,", PY),
 # the ticket
 (TM, "  if (most && t.lots * spec.lot_size > most) {", "  if (most && t.lots * spec.lot_size > most + spec.lot_size) {", UI),
 (TT, "          {spec.freeze_qty ? ` · max ${fmtQty(spec.freeze_qty)} per order` : \"\"}", "          {\"\"}", UI),
]
for cmd, cwd in (PY, UI):
    if subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd).returncode != 0:
        raise SystemExit(f"the tests fail before any mutation ({' '.join(cmd)}): fix that first")
caught = 0
for f, a, b, (cmd, cwd) in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
