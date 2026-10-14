import subprocess, pathlib
B="src/widgets/books/"
M = [
 (B+"model.ts", 'return o.status === "PENDING" || o.status === "OPEN" || o.status === "PARTIAL";', 'return o.status === "OPEN" || o.status === "PARTIAL";', "pending-new is working"),
 (B+"model.ts", 'return inTab(o, "working") && o.pending === "NONE";', 'return inTab(o, "working");', "no double cancel"),
 (B+"model.ts", 'if (o.pending === "CANCEL") return "Pending cancel";', '', "pending cancel shown"),
 (B+"model.ts", 'const added = incoming.filter((e) => !seen.has(e.seq));', 'const added = incoming;', "no duplicate events"),
 (B+"model.ts", "const safe = /^[=+\\-@\\t\\r]/.test(s) ? `'${s}` : s;", "const safe = s;", "csv formula guard"),
 (B+"model.ts", 'if (!id.toUpperCase().includes(contract.trim().toUpperCase())) return false;', '', "contract filter"),
 (B+"model.ts", '.sort((a, b) => b.seq - a.seq);', '.sort((a, b) => a.seq - b.seq);', "newest first"),
 (B+"model.ts", 'if (alerts > 0) {', 'if (false) {', "differs wins"),
 (B+"OrderBook.tsx", 'const why = cancelBlock(store.getState(), accountId);', 'const why = cancelBlock(store.getState(), accountId) ?? (store.getState().books[accountId ?? ""]?.kill.engaged ? "stopped" : null);', "cancel while stopped"),
 (B+"AuditTrail.tsx", 'if (!live || !base) return;', 'if (!base) return;', "paused means paused"),
 (B+"AuditTrail.tsx", 'if (m.account_id === accountId) soon();', 'soon();', "only this account"),
 (B+"AuditTrail.tsx", 'setOlder(page.length === PAGE);\n        take(page);\n      })\n      .catch((e: unknown) => setError', 'setOlder(true);\n        take(page);\n      })\n      .catch((e: unknown) => setError', "no Older after a short page"),
 (B+"Positions.tsx", 'className={`row book-row positions-row${alert ? " differs" : ""}`}', 'className="row book-row positions-row"', "differs row marked"),
]
caught=0
for path, old, new, name in M:
    p=pathlib.Path(path); src=p.read_text()
    if old not in src:
        print("NOT FOUND", name); continue
    try:
        p.write_text(src.replace(old,new,1))
        ok = subprocess.run(["npx","vitest","run","src/widgets/books"],capture_output=True,text=True).returncode != 0
    finally:
        p.write_text(src)
    caught+=ok
    print(("CAUGHT  " if ok else "MISSED  ")+name, flush=True)
print(f"{caught}/{len(M)} caught")
