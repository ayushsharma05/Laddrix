"""#232 mutation check: break one rule of the chart screen (model, drawing, widget), the ticket's
price pick, the candle stream or the tab title; the UI tests must notice. Restores each file in
`finally`; a hang counts as caught. Run alone. Every anchor is checked to be unique first."""
import subprocess

M = "ui/src/widgets/chart/model.ts"
D = "ui/src/widgets/chart/draw.ts"
C = "ui/src/widgets/chart/Chart.tsx"
T = "ui/src/widgets/ticket/Ticket.tsx"
L = "ui/src/workspace/links.ts"
S = "ui/src/api/stream.ts"
P = "ui/src/workspace/Panel.tsx"
CMD = ["npx", "vitest", "run", "src/widgets/chart", "src/widgets/ticket", "src/api", "src/workspace"]
MUTANTS = [
 (M, "  if (!last || c.t > last.t) return [...list, c];", "  if (!last) return [...list, c];"),
 (M, "  if (c.t === last.t) return [...list.slice(0, -1), c];", "  if (false) return [...list.slice(0, -1), c];"),
 (M, "older.filter((c) => c.t < first)", "older.filter((c) => c.t <= first)"),
 (M, "  const to = Math.max(0, n - view.offset);", "  const to = n;"),
 (M, "  return { ...view, offset: Math.min(most, Math.max(0, Math.round(view.offset + bars))) };", "  return { ...view, offset: Math.max(0, Math.round(view.offset + bars)) };"),
 (M, "  return { ...view, barW: Math.min(MAX_BAR, Math.max(MIN_BAR, view.barW * factor)) };", "  return { ...view, barW: Math.max(MIN_BAR, view.barW * factor) };"),
 (M, "  return visible(view, n, plotW).from < margin;", "  return visible(view, n, plotW).from <= margin;"),
 (M, "  const pad = max > min ? (max - min) * 0.06 :", "  const pad = max > min ? 0 :"),
 (M, "  return Math.max(tick, Math.round(paise / tick) * tick);", "  return Math.round(paise / tick) * tick;"),
 (M, "  const ms = Date.parse(t) + IST_OFFSET_MS;", "  const ms = Date.parse(t);"),
 (M, "    if (newDay && !daily) out.push", "    if (newDay) out.push"),
 (M, "o.day && Math.abs(o.i - l.i) < step)", "o.day && Math.abs(o.i - l.i) < 0)"),
 (M, "    if (hi < 0 || at < starts[0]!) continue;", "    if (hi < 0) continue;"),
 (M, "  const i = to - Math.ceil((plotW - x) / view.barW);", "  const i = to - Math.floor((plotW - x) / view.barW);"),
 (D, "    if (up) ctx.strokeRect(", "    if (false) ctx.strokeRect("),
 (D, "      const y = buy ? scale.y(c.l) + 8 + k * 9 : scale.y(c.h) - 8 - k * 9;", "      const y = buy ? scale.y(c.h) - 8 - k * 9 : scale.y(c.l) + 8 + k * 9;"),
 (D, "    if (i < from || i >= to) continue;\n    const c = f.candles[i]!;", "    if (false) continue;\n    const c = f.candles[i]!;"),
 (D, "      ctx.fillText(fmtPrice(f.tick ? snapTick(at, f.tick) : at), plotW + 6, y);", "      ctx.fillText(fmtPrice(at), plotW + 6, y);"),
 (D, "  if (y < 0 || y > priceH) return null;", "  if (y < 0) return null;"),
 (C, "      setView((v) => pan({ ...v, offset: start.offset }, bars, candles.length, plotW));", "      setView((v) => pan({ ...v, offset: drag.current!.offset }, bars, candles.length, plotW));"),
 (C, "    if (dragged.current) {\n      dragged.current = false;", "    if (false) {\n      dragged.current = false;"),
 (C, "        if (load.current !== mine) return; // for an interval no longer shown", "        // (no check)"),
 (C, "        setCandles((now) => mergeOlder(a.candles, now)); // a stream candle may have come first\n        setOlderState(\"idle\");", "        setCandles((now) => mergeOlder(a.candles, now)); // a stream candle may have come first"),
 (C, "    if (!interval || !candles.length || olderState !== \"idle\" ||", "    if (!interval || !candles.length ||"),
 (C, "    if ((params.link ?? \"none\") === \"none\") {", "    if (false) {"),
 (C, "    if (x > plotW) return;\n    const at = priceAtY", "    const at = priceAtY"),
 (T, "      if (pick.instrumentId !== id || pick.seq <= takenPick.current) return;", "      if (pick.seq <= takenPick.current) return;"),
 (T, "      if (!needsPrice(type)) {\n        const priced", "      if (false) {\n        const priced"),
 (L, "    return pick && now - pick.at <= PICK_FRESH_MS ? pick : undefined;", "    return pick;"),
 (S, "      for (const key of this.candles.keys()) {", "      for (const key of [] as string[]) {"),
 (S, "      if (set!.size === 0 && this.candles.get(key) === set) {", "      if (false) {"),
 (P, '    const sub = api.onDidTitleChange((e) => setSeen({ api, title: e.title }));', '    const sub = { dispose() {} };'),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(CMD, capture_output=True, timeout=300, cwd="ui")
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
