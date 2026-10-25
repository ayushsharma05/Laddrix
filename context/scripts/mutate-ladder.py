import subprocess, sys, pathlib
M = [
 ("src/widgets/ladder/model.ts", "return Math.round(pricePaise / tickPaise) * tickPaise;", "return Math.floor(pricePaise / tickPaise) * tickPaise;", "snap rounds"),
 ("src/widgets/ladder/model.ts", "if (traded && tick.ltp_paise === price) ahead", "if (traded) ahead", "queue eaten only at my price"),
 ("src/widgets/ladder/model.ts", "if (shown !== null) ahead = Math.min(ahead, Math.max(0, shown - mine));", "", "queue capped by depth"),
 ("src/widgets/ladder/model.ts", "ahead: shown === null ? null : Math.max(0, shown - mine),", "ahead: shown,", "late-found order at back"),
 ("src/widgets/ladder/model.ts", "return improves || inside ? 0 : null;", "return 0;", "unknown beyond depth"),
 ("src/widgets/ladder/model.ts", 'market_protection: args.type.market_protection === "auto" ? -1 : null,', 'market_protection: null,', "market protection"),
 ("src/widgets/ladder/model.ts", "return rowsAway > Math.max(1, Math.floor(count / 4));", "return rowsAway > 0;", "recentre band"),
 ("src/widgets/ladder/model.ts", "if (tick.volume < this.volume) {", "if (false) {", "VAP new day"),
 ("src/widgets/ladder/Ladder.tsx", "const why = cancelBlock(store.getState(), accountId);", "const why = tradingBlock(store.getState(), accountId);", "cancel allowed while stopped"),
 ("src/widgets/ladder/Ladder.tsx", "if (unconfirmed(state.books[accountId]?.orders ?? {}, id).length) {", "if (false) {", "unconfirmed blocks"),
 ("src/widgets/ladder/Ladder.tsx", "if (!armed || armed.side !== side || armed.price !== px || armed.until < now) {", "if (!armed) {", "confirm same cell"),
 ("src/widgets/ladder/Ladder.tsx", '      if (!orderType || !product) return warn("not sent: what can be ordered here hasn\'t loaded yet");\n      if (lots <= 0) return warn', '      if (!orderType || !product) return warn("not sent: what can be ordered here hasn\'t loaded yet");\n      if (lots < 0) return warn', "zero lots"),
 ("src/state/gate.ts", "if (!account.can_trade) return `you can view", "if (false) return `you can view", "view-only"),
]
caught = 0
for path, old, new, name in M:
    p = pathlib.Path(path); src = p.read_text()
    assert old in src, name
    try:
        p.write_text(src.replace(old, new, 1))
        r = subprocess.run(["npx", "vitest", "run", "src/widgets/ladder", "src/state"], capture_output=True, text=True)
        ok = r.returncode != 0
    finally:
        p.write_text(src)
    caught += ok
    print(("CAUGHT  " if ok else "MISSED  ") + name, flush=True)
print(f"{caught}/{len(M)} caught")
