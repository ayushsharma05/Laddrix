"""#234 mutation check: break one rule of the market screens (time & sales, movers, the depth and
tape models, the alerts manager, the hooks that keep a ticket on the right contract, the option
prices in the simulator); the Python or UI tests must notice. Restores each file in `finally`; a
hang counts as caught. Run alone. Every anchor is checked to be unique first."""
import subprocess

PR = "src/mocktrade/marketdata/prints.py"
MV = "src/mocktrade/marketdata/movers.py"
SI = "src/mocktrade/marketdata/simulator.py"
SV = "src/mocktrade/marketdata/service.py"
ROUTES_MARKET = "src/mocktrade/server/routes/market.py"
MM = "ui/src/widgets/market/model.ts"
TS = "ui/src/widgets/market/TimeAndSales.tsx"
MO = "ui/src/widgets/market/Movers.tsx"
AM = "ui/src/widgets/alerts/model.ts"
CX = "ui/src/app/context.tsx"
ST = "ui/src/state/store.ts"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_market_screens.py", "tests/test_simulator.py",
       "tests/test_market_data.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/market", "src/widgets/alerts", "src/widgets/ticket",
       "src/state", "src/app"], "ui")
MUTANTS = [
 (PR, "        if before is None or tick.volume <= before.volume:", "        if before is None or tick.volume < before.volume:"),
 (PR, "        if not self._wanted(i):\n", "        if False:\n"),
 (PR, "        if before.ask is not None and price >= before.ask:", "        if before.ask is not None and price > before.ask:"),
 (PR, "        elif before.bid is not None and price <= before.bid:", "        elif before.bid is not None and price < before.bid:"),
 (PR, "price, tick.volume - before.volume, side))", "price, tick.volume, side))"),
 (PR, "        fresh = [p for p in tape if p.seq > after]", "        fresh = [p for p in tape if p.seq >= after]"),
 (PR, "        return fresh[-limit:]", "        return fresh[:limit]"),
 (MV, "        if kept is None or kept[0] != today:", "        if kept is None:"),
 (MV, "            if exchange and i.exchange.value != exchange:\n                continue", "            if False:\n                continue"),
 (MV, "            if kind and i.kind.value != kind:\n                continue", "            if False:\n                continue"),
 (MV, "            if t.open_interest is not None and base and base[0] == today and base[1] > 0:", "            if t.open_interest is not None and base and base[1] > 0:"),
 (MV, "            have = [r for r in have if r[key] > 0]", "            have = [r for r in have if r[key] >= 0]"),
 (MV, "            have = [r for r in have if r[key] != 0]", "            have = [r for r in have]"),
 (MV, "            have.sort(key=lambda r: (-abs(r[key]), r[\"instrument_id\"]))", "            have.sort(key=lambda r: (-r[key], r[\"instrument_id\"]))"),
 (MV, "            sign = 1 if by == \"losers\" else -1", "            sign = -1"),
 (MV, "        if not 1 <= limit <= MAX_LIMIT:", "        if not 1 <= limit:"),
 (SI, "                    walk.anchor = walk.ltp = walk.open = priced", "                    walk.ltp = priced"),
 (SV, "        return bool(self._watchers.get(instrument_id))", "        return True"),
 (ROUTES_MARKET, "        rows = mine.prints.recent(instrument_id, after=max(0, after),", "        rows = mine.prints.recent(instrument_id, after=0,"),
 (ROUTES_MARKET, "        except MoversError as exc:\n            raise HTTPException(422, str(exc)) from None", "        except MoversError as exc:\n            return {\"detail\": str(exc)}"),
 (MM, "  const most = Math.max(1, ...tick.bids.map((l) => l[1]), ...tick.asks.map((l) => l[1]));", "  const most = Math.max(1, ...tick.bids.map((l) => l[1]));"),
 (MM, "  const b = tick.buy_qty ?? bidTotal;", "  const b = bidTotal;"),
 (MM, "ticks: Math.round((offer.price - best.price) / tickPaise) }", "ticks: offer.price - best.price }"),
 (MM, "  const add = fresh.filter((p) => p.seq > newest)", "  const add = fresh.filter((p) => p.seq >= newest)"),
 (MM, "[...add, ...tape].slice(0, TAPE_MAX)", "[...add, ...tape]"),
 (MM, "  const rows = minQty > 0 ? tape.filter((p) => p.qty >= minQty) : tape;", "  const rows = minQty > 0 ? tape.filter((p) => p.qty > minQty) : tape;"),
 (MM, "    else if (p.side === \"SELL\") sold += p.qty;", "    else sold += p.qty;"),
 (TS, "after=${newest.current}&limit=300", "after=0&limit=300"),
 (MO, "      asked.current === query ? MOVERS_POLL_MS : 0,", "      MOVERS_POLL_MS,"),
 (MO, "  const shown = answer?.by === by ? answer : null;", "  const shown = answer;"),
 (AM, "  if (!spec.repeat.includes(t.repeat)) problems.push", "  if (false) problems.push"),
 (AM, "cooldown < spec.cooldown_s.min || cooldown > spec.cooldown_s.max)", "cooldown < spec.cooldown_s.min)"),
 (AM, "  if (t.note.length > spec.note_max) problems.push", "  if (false) problems.push"),
 (AM, "  if (a.status.startsWith(\"fired\")) return \"good\";", "  if (a.status.startsWith(\"fired\")) return \"pending\";"),
 (AM, "  const next = at >= 0 ? list.map((x, k) => (k === at ? a : x)) : [...list, a];", "  const next = [...list, a];"),
 (CX, "  return tick && tick.instrument_id === instrumentId ? tick : null;", "  return tick;"),
 (CX, "  if (found && found.id === instrumentId) return found;", "  if (found) return found;"),
 (CX, "  if (found && found.key === key) return found.spec;", "  if (found) return found.spec;"),
 (ST, "        this.addMessage(\"WARN\", m.alert.instrument_id, `Alert: ${m.text}`);", "        // (no message)"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    cmd, cwd = UI if f.startswith("ui/") else PY
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
