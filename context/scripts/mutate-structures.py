"""#428 mutation check: break one rule of structure positions (an Autospreader's fills made
into its spread's structure once, never twice; hand allocation's limits; remove and move;
whole spread lots and residuals; each leg's P&L at its order's average; unallocated; the
audit notes read back; the routes; the screen); the tests must notice. Restores each file in
`finally`; a hang counts as caught. Run alone from the repo root."""
import subprocess

B = "src/mocktrade/structures/book.py"
P = "src/mocktrade/platform.py"
ROUTES_SPREADS = "src/mocktrade/server/routes/spreads.py"
W = "ui/src/widgets/structures/Structures.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_structures.py", "tests/test_workspaces.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/structures"], "ui")
MUTANTS = [
 # automatic
 (B, "        delta = o.filled_qty - self._auto.get(o.client_order_id, 0)", "        delta = o.filled_qty", PY),
 (B, "        s = next((x for x in self._structures.values() if x.spread_id == spread.spread_id), None)", "        s = None", PY),
 (B, "                    self._auto[cid] = self._auto.get(cid, 0) + int(x[\"qty\"])", "                    pass", PY),
 # hand allocation
 (B, "        if qty > free:\n            raise StructureError", "        if False:\n            raise StructureError", PY),
 (B, "        if qty % lot:", "        if False:", PY),
 (B, "        if o.request.instrument_id not in {g.instrument_id for g in s.legs}:\n            raise", "        if False:\n            raise", PY),
 (B, "        if qty > there:", "        if False:", PY),
 (B, "        self._check_remove(a, client_order_id, qty)\n        self._check_leg(b, client_order_id)\n        self._allocate(a, client_order_id, -qty, user_id)", "        self._check_remove(a, client_order_id, qty)\n        self._check_leg(b, client_order_id)\n        pass", PY),
 (B, "        if any(s.allocated.values()):", "        if False:", PY),
 # what it holds
 (B, "        if positive:\n            lots = min(counts)", "        if positive:\n            lots = max(counts)", PY),
 (B, "        else:\n            lots = 0", "        else:\n            lots = min(counts)", PY),
 (B, "            \"residual_qty\": pos.qty - whole,", "            \"residual_qty\": 0,", PY),
 (B, "    pos.realised += (price - avg) * closing * direction", "    pos.realised += price * closing * direction", PY),
 (B, "    pos.cost = avg * pos.qty\n    rest", "    pos.cost = price * pos.qty\n    rest", PY),
 (B, "            rest = self._net_qty(i) - held.get(i, 0)", "            rest = self._net_qty(i)", PY),
 (B, "            free = self._unallocated_of(o)\n            if free > 0:", "            free = o.filled_qty\n            if free > 0:", PY),
 # the audit notes
 (B, "            elif e.kind == \"STRUCTURE_DELETED\":\n                self._structures.pop(x[\"structure_id\"], None)", "            elif e.kind == \"STRUCTURE_DELETED\":\n                pass", PY),
 (P, "            structures.load(log_.notes(account_id, list(STRUCTURE_NOTES)))", "            pass", PY),
 # routes
 (ROUTES_SPREADS, "        spread = book.visible(user, PREFIX + body.spread_id)\n        if spread is None:\n            raise HTTPException(404, f\"no spread {body.spread_id}\")\n        legs", "        spread = book.get(body.spread_id)\n        if spread is None:\n            raise HTTPException(404, f\"no spread {body.spread_id}\")\n        legs", PY),
 (ROUTES_SPREADS, '            raise HTTPException(404 if exc.missing else 422, exc.reason) from None\n\n    @api.get("/api/accounts/{account_id}/structures")', '            raise HTTPException(422, exc.reason) from None\n\n    @api.get("/api/accounts/{account_id}/structures")', PY),
 # the screen
 (W, "                      {qty(a.qty)} {a.instrument_id ? shortName(a.instrument_id) : \"\"}", "                      {a.instrument_id ? shortName(a.instrument_id) : \"\"}", UI),
 (W, "{ to: e.target.value, client_order_id: a.client_order_id, qty: a.qty }", "{ to: e.target.value, client_order_id: a.client_order_id, qty: 0 }", UI),
 (W, "onAdd(e.target.value, Number(n))", "onAdd(e.target.value, fill.unallocated_qty)", UI),
 (W, "  useEffect(load, [load, orders, positions]);", "  useEffect(load, [load]);", UI),
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
