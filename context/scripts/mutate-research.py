"""#226 mutation check: break one research-screen rule; the tests must notice.
Server mutants run tests/test_backtest.py, screen mutants the research widget tests.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files). Anchor each mutant so its text is
unique: a shorter line can hide inside a more indented one earlier in the file."""
import subprocess

B = "src/laddrix/research/backtest.py"
A = "src/laddrix/server/routes/research.py"
M = "ui/src/widgets/research/model.ts"
W = "ui/src/widgets/research/Backtests.tsx"
R = "ui/src/widgets/research/Recordings.tsx"
C = "ui/src/widgets/research/LineChart.tsx"
PY = ["python3", "-m", "pytest", "-q", "-x", "tests/test_backtest.py"]
UI = ["npx", "vitest", "run", "src/widgets/research"]
MUTANTS = [
 # the server
 (B, '        if spec.algo not in {t.spec.code for t in BUILTIN}:', '        if False:', PY),
 (B, '                if "skipped" in d:\n                    raise LookupError', '                if False:\n                    raise LookupError', PY),
 (B, '        if run.result is None:\n            raise LookupError', '        if False:\n            raise LookupError', PY),
 (B, '        "days": [x.isoformat() for x in reversed(ticks_days)],  # newest first', '        "days": [x.isoformat() for x in ticks_days],  # newest first', PY),
 (B, '        return sorted((p.stem for p in folder.glob("*.json") if _DAY_FILE.match(p.stem)),\n                      reverse=True)', '        return sorted((p.stem for p in folder.glob("*.json") if _DAY_FILE.match(p.stem)),\n                      reverse=False)', PY),
 (A, '        if own is not None and instrument_id:\n            out["candle_days"]', '        if own is not None:\n            out["candle_days"]', PY),
 (A, '        if format == "csv":\n            return Response(await asyncio.to_thread(report_csv, data),', '        if format == "csv":\n            return Response(await asyncio.to_thread(report_pdf, data),', PY),
 # the screens' model
 (M, '  while (v >= 1024 && u < units.length - 1) {', '  while (v >= 1024) {', UI),
 (M, '  const days = [...t.days].sort(); // a run goes through its days in order', '  const days = [...t.days];', UI),
 (M, '  if (t.days.length > t.form.max_days) problems.push', '  if (false) problems.push', UI),
 (M, '  let min = Math.min(0, ...values);', '  let min = Math.min(...values);', UI),
 (M, '      if (v === null) {\n        pen = false; // a gap: lift the pen', '      if (v === null) {\n        pen = true; // a gap: lift the pen', UI),
 (M, '  const free = next.indexOf(null);', '  const free = next.lastIndexOf(null);', UI),
 (M, '  if (at >= 0) {\n    next[at] = null;\n    return next;\n  }', '  if (at >= 0) {\n    return next.filter((x) => x !== runId);\n  }', UI),
 (M, '  return candles ? (form.candle_days ?? []) : form.days;', '  return form.days;', UI),
 (M, '  const skipped = r.summary?.days_skipped.length ?? 0;', '  const skipped = 0;', UI),
 # (capital ≤ 0 needs no mutant: parsePrice never returns 0 or less, so there is no such check)
 # the screens
 (W, '    if (!going) return;\n    const t = setTimeout', '    const t = setTimeout', UI),
 (W, '  const version = `${run.run_id}|${run.state}|${run.progress}`;', '  const version = run.run_id;', UI),
 (W, '  const days = useMemo(() => picked.filter((d) => offered.includes(d)), [picked, offered]);', '  const days = picked;', UI),
 (W, '                    disabled={!r.summary || full}', '                    disabled={false}', UI),
 (W, '      setParams({ mode: "runs" });\n      refresh();', '      refresh();', UI),
 (W, '        if (e instanceof ApiError && e.status === 404) setOff(e.message);', '        if (false) setOff(e.message);', UI),
 (R, '                    .sort((a, b) => b[1] - a[1])', '', UI),
 (C, '          onMouseLeave={() => setHover(null)}', '', UI),
 (C, '        {series.length > 1 && (\n          <span className="chart-legend"', '        {series.length > 99 && (\n          <span className="chart-legend"', UI),
]
caught = 0
for f, a, b, cmd in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180, cwd="ui" if cmd is UI else None)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
