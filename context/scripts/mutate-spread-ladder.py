"""#416 mutation check: break one rule of the spread ladder (its working orders from the ASE
algos, a click starting the algo with the spread's side, lots and price, cancel stopping it,
move stopping and restarting the lots left, no order spec or VAP asked, the title, the
right-click "Autospreader settings…", the Autospreader's Ladder button; the server's spread
lots and a tradable spread); the tests must notice. Restores each file in `finally`; a hang
counts as caught. Run alone from the repo root."""
import subprocess

M = "ui/src/widgets/ladder/model.ts"
L = "ui/src/widgets/ladder/Ladder.tsx"
A = "ui/src/widgets/ase/Autospreader.tsx"
S = "src/mocktrade/algos/autospreader.py"
P = "src/mocktrade/server/app.py"
UI = (["npx", "vitest", "run", "src/widgets/ladder", "src/widgets/ase"], "ui")
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_autospreader.py", "tests/test_spread_api.py"], ".")
MUTANTS = [
 (M, '    .filter((a) => a.code === "ASE" && a.params.spread === spreadId && (a.state === "WORKING" || a.state === "PAUSED" || a.state === "STOPPING"))', '    .filter((a) => a.code === "ASE" && a.params.spread === spreadId)', UI),
 (M, '      const side: Side = a.extra.spread_side === "SELL" ? "SELL" : "BUY";', '      const side: Side = a.side;', UI),
 (M, '          price_paise: Number(a.params.level_paise),', '          price_paise: 0,', UI),
 (M, '        pending: a.state === "STOPPING" ? "CANCEL" : "NONE",', '        pending: "NONE",', UI),
 (M, '    .filter((o) => o.request.qty > o.filled_qty);', '', UI),
 (M, '        status: done > 0 ? "PARTIAL" : "OPEN",', '        status: "OPEN",', UI),
 (L, '  const working = useMemo(() => (spread ? spreadWorking(algos ?? {}, id) : workingOrders(orders ?? {}, id)), [spread, algos, orders, id]);', '  const working = useMemo(() => workingOrders(orders ?? {}, id), [orders, id]);', UI),
 (L, '          side,\n          lots: lotsNow,\n          price_paise: at,', '          side,\n          lots: lots,\n          price_paise: at,', UI),
 (L, '        return sendSpread(side, ctrl ? best! : at, lots);', '        return sendSpread(side, at, 1);', UI),
 (L, '            .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos/${o.request.client_order_id}/stop`, {})\n            .then((view) => store.applyAlgos(accountId, [view]))', '            .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos/${o.request.client_order_id}/pause`, {})\n            .then((view) => store.applyAlgos(accountId, [view]))', UI),
 (L, '          const rest_lots = o.request.qty - o.filled_qty;', '          const rest_lots = o.request.qty;', UI),
 (L, '              sendSpread(o.request.side, to, rest_lots);', '              sendSpread(o.request.side, from, rest_lots);', UI),
 (L, '    if (spread) return;\n    let live = true;', '    let live = true;', UI),
 (L, '  const spec = useOrderSpec(accountId, spread ? undefined : id);', '  const spec = useOrderSpec(accountId, id);', UI),
 (L, '    if (spread && params.label !== instrument.symbol) setParams({ label: instrument.symbol });', '', UI),
 (L, '  tabTitle: (p) => (p.instrumentId ? (isSpread(p.instrumentId) && p.label ? p.label : shortName(p.instrumentId)) : undefined),', '  tabTitle: (p) => (p.instrumentId ? shortName(p.instrumentId) : undefined),', UI),
 (L, '? [{ label: "Autospreader settings…", onSelect: () => act.open("ase", { mode: "edit", spreadId: p.instrumentId!.slice(SPREAD_PREFIX.length) }) }, "separator" as const]', '? []', UI),
 (L, '          {spread ? (\n            <span className="ladder-spread-note"', '          {false ? (\n            <span className="ladder-spread-note"', UI),
 (A, '            onLadder={(s) => open("ladder", { instrumentId: s.instrument_id, link: "none" })}', '            onLadder={() => undefined}', UI),
 (S, '            lots=ctx.qty // per_lot,', '            lots=ctx.qty,', PY),
 (S, '            lots_done=self._net(ctx, self.q) // per_lot,', '            lots_done=0,', PY),
 (P, '        "tradable": True,  # a spread ladder', '        "tradable": False,  # a spread ladder', PY),
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
