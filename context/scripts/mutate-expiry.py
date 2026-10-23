"""#290 mutation check: break one rule of F&O expiry day (a contract ends at 15:30 on its
expiry date; "expires today" only before that; a broker's cut-off leaves only closing trades,
and only for orders that add risk; stock F&O settle by delivery; an option settles at what it
is in the money by; expired positions settle at the next day's roll, before it, in our book and
at the paper broker; a restart keeps the noted settlement or settles what expired while it was
off; search and the chain drop expired contracts); the tests must notice. Runs the tests
unmutated first. Restores each file. Run alone."""
import subprocess

E = "src/mocktrade/refdata/expiry.py"
C = "src/mocktrade/risk/checks.py"
P = "src/mocktrade/platform.py"
R = "src/mocktrade/recovery.py"
S = "src/mocktrade/refdata/store.py"
O = "src/mocktrade/options/chain.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_expiry.py", "tests/test_server.py"]
MUTANTS = [
 (E, "        expired = now >= expires_at", "        expired = now > expires_at"),
 (E, "            expires_today=today and not expired,", "            expires_today=today,"),
 (E, "            opening_closed=cutoff is not None and now >= cutoff,", "            opening_closed=False,"),
 (E, "        return lambda i: i.expiry is not None and i.expiry < day", "        return lambda i: False"),
 (E, "            return max(under - instrument.strike_paise, 0)", "            return last(instrument.id)"),
 (E, "        return max(instrument.strike_paise - under, 0)", "        return last(instrument.id)"),
 (E, "            i.kind is InstrumentKind.EQUITY", "            False"),
 (C, "            or self._expiry_problem(instrument, request, request.qty, others)\n", ""),
 (C, "        if info.expired:\n", "        if False:\n"),
 (C, "        if not info.opening_closed or not grows:", "        if not info.opening_closed:"),
 (C, "        if request.reduce_only or self._closes(request, leaves, others):\n            return None\n        cutoff", "        if request.reduce_only:\n            return None\n        cutoff"),
 (P, "            expiry=self.expiries.info,", ""),
 (P, "            settled = self._settle_expired(account_id, runtime, today)\n            carried = runtime.positions.roll()", "            carried = runtime.positions.roll()\n            settled = self._settle_expired(account_id, runtime, today)"),
 (P, "                settle_at_broker(p.instrument_id, p.product, price)", "                pass"),
 (R, "            if e.ts < since:", "            if False:"),
 (R, "            if p.qty and expired(p.instrument_id):", "            if False:"),
 (S, "            if i.symbol.upper().startswith(needle) and (keep is None or keep(i))", "            if i.symbol.upper().startswith(needle)"),
 (O, "                and not self._expiries.expired(i, now)", "                and True"),
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
