"""#202 mutation check on the Orders+ screens: break one rule, a UI test must notice.
Run from ui/: `cd ui && python3 ../context/scripts/mutate-orders-plus-ui.py`. Run alone."""
import subprocess
M_, B, A, T = "src/widgets/gtt/model.ts", "src/widgets/gtt/GttBook.tsx", "src/widgets/algos/model.ts", "src/widgets/ticket/Ticket.tsx"
M = [
 (M_, "  const count = spec.legs[kind] ?? 1;", "  const count = 1;"),
 (M_, "side === \"SELL\" ? trigger - 5 * t : trigger + 5 * t", "trigger - 5 * t"),
 (M_, "      if (gap < spec.min_distance_pct) {", "      if (false) {"),
 (M_, "    else if (trigger % spec.tick_paise)", "    else if (false)"),
 (M_, "qty: lots * spec.lot_size", "qty: lots"),
 (M_, "  if (parsed.length === 2 && parsed[0]!.trigger_paise >= parsed[1]!.trigger_paise) {", "  if (false) {"),
 (M_, "    const activeFirst = Number(b.status === \"active\") - Number(a.status === \"active\");", "    const activeFirst = 0;"),
 (M_, "  if (g.status === \"triggered\") return g.order_id ? \"good\" : \"buy\";", "  if (g.status === \"triggered\") return \"buy\";"),
 (B, "setLegs((old) => (old.every((l) => l.trigger === \"\") ? defaultLegs(spec, kind, ltp, side) : old));", "setLegs(defaultLegs(spec, kind, ltp, side));"),
 (B, "disabled={!current || current.status !== \"active\"}", "disabled={!current}"),
 (A, "    const family = s.family ?? \"execution\";", "    const family = \"execution\";"),
 (T, "  if (gtt) tabs.push([\"gtt\", \"GTT\",", "  if (true) tabs.push([\"gtt\", \"GTT\","),
 (T, "            defaultLots={lots || 1}\n            onStarted", "            defaultLots={1}\n            onStarted"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["npx", "vitest", "run", "--maxWorkers=2", "src/widgets/gtt", "src/widgets/algos", "src/widgets/ticket"],
                           capture_output=True, timeout=180)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:80])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
