"""#429 mutation check: break one rule of spread charts (a bucket only when every leg has one,
gaps counted never filled, open and close from each leg's with its price×, high and low from
them, no volume, live once every leg is, the forming candle, the owner-only route and stream,
the legs fetched together, the screen's label, the window titled with the spread's name and
the Autospreader's Chart button); the tests must notice. Restores each
file in `finally`; a hang counts as caught. Run alone from the repo root."""
import subprocess

C = "src/mocktrade/charts/service.py"
ROUTES_MARKET = "src/mocktrade/server/routes/market.py"
T = "src/mocktrade/server/stream.py"
CH = "ui/src/widgets/chart/Chart.tsx"
AW = "ui/src/widgets/ase/Autospreader.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_spread_charts.py", "tests/test_charts.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/chart", "src/widgets/ase"], "ui")
MUTANTS = [
 (C, "    if any(c is None for _, c in legs):\n        return None", "    if all(c is None for _, c in legs):\n        return None", PY),
 (C, "    if len(starts) != 1:\n        return None", "    if not starts:\n        return None", PY),
 (C, "    o = sum(m * c[\"o\"] for m, c in legs if c is not None)", "    o = sum(c[\"o\"] for m, c in legs if c is not None)", PY),
 (C, "    cl = sum(m * c[\"c\"] for m, c in legs if c is not None)", "    cl = sum(m * c[\"o\"] for m, c in legs if c is not None)", PY),
 (C, "\"h\": max(o, cl), \"l\": min(o, cl)", "\"h\": max(o, cl), \"l\": max(o, cl)", PY),
 (C, "\"c\": cl, \"v\": 0,", "\"c\": cl, \"v\": 10,", PY),
 (C, "        if candle is None:\n            gaps += 1", "        if candle is None:\n            pass", PY),
 (C, "        \"live_from\": None if None in lives else max(lives),", "        \"live_from\": max((x for x in lives if x), default=None),", PY),
 (C, "    return combine([(leg.multiplier, charts.forming(leg.instrument_id, code))", "    return combine([(1, charts.forming(leg.instrument_id, code))", PY),
 (C, "    per_leg = await asyncio.gather(\n        *(charts.candles(leg.instrument_id, code, start, end) for leg in spread.legs)\n    )", "    per_leg = [await charts.candles(leg.instrument_id, code, start, end) for leg in spread.legs]", PY),
 (ROUTES_MARKET, "                spread = book.visible(user, instrument_id)\n                if spread is None:", "                spread = book.of_instrument(instrument_id)\n                if spread is None:", PY),
 (T, "        spread = self._spread_of(connection.user, instrument_id)\n", "        spread = None\n", PY),
 (CH, "            spread mark, not traded prices{answer.gaps ? ` · ${answer.gaps} gaps` : \"\"}", "            spread mark, not traded prices", UI),
 (CH, "    if (name && params.label !== name) setParams({ label: name });", "    if (name && params.label === undefined) setParams({ label: name });", UI),
 (CH, "    if (name && params.label !== name) setParams({ label: name });", "    if (params.label !== name) setParams({ label: name ?? \"\" });", UI),
 (CH, "`${isSpread(p.instrumentId) && p.label ? p.label : shortName(p.instrumentId)} ${p.interval ?? \"\"}`", "`${p.label ? p.label : shortName(p.instrumentId)} ${p.interval ?? \"\"}`", UI),
 (AW, "            onChart={(s) => open(\"chart\", { instrumentId: s.instrument_id, link: \"none\" })}", "            onChart={(s) => open(\"ladder\", { instrumentId: s.instrument_id, link: \"none\" })}", UI),
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
