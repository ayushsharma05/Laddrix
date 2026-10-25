"""#450 mutation check: break one of the places the screens now work out while drawing rather
than reset from an effect (each value kept with what it is for; a confirmation for what was
shown; a reset when an input changes; the newest-value refs); the screens' tests must notice.
Runs them unmutated first. Restores each file. Run alone, from the repo root."""
import subprocess

TESTS = ["npx", "vitest", "run"]
U = "ui/src/"
MUTANTS = [
 (U + "app/context.tsx", "(found && found.id === instrumentId ? found : null);", "(found ? found : null);"),
 (U + "app/context.tsx", "(found && found.key === key ? found.spec : null);", "(found ? found.spec : null);"),
 (U + "app/context.tsx", "      setTick(null); // switching back later", "      // switching back later"),
 (U + "widgets/algos/AlgoManager.tsx", "return state && state.accountId === accountId ? state :", "return state ? state :"),
 (U + "widgets/gtt/GttBook.tsx", "return state && state.key === key ? state :", "return state ? state :"),
 (U + "widgets/gtt/GttBook.tsx", "if (ltp !== null && legs.every((l) => l.trigger === \"\")) setLegs(", "if (ltp !== null) setLegs("),
 (U + "widgets/chain/OptionChain.tsx", "const expiries = dates && dates.underlying === underlying ? dates.expiries : NONE;", "const expiries = dates ? dates.expiries : NONE;"),
 (U + "widgets/ticket/useChargeEstimate.ts", "return bill && bill.key === key ? bill.bill : null;", "return bill ? bill.bill : null;"),
 (U + "widgets/ticket/Ticket.tsx", "  if (priceText === \"\" && ltp !== null) setPriceText(", "  if (false) setPriceText("),
 (U + "shell/StopBand.tsx", "const armed = armedFor !== null && armedFor.positions === positions;", "const armed = armedFor !== null;"),
 (U + "widgets/books/SquareOff.tsx", "&& armedFor.openMis === openMis && armedFor.accountId === accountId;", ";"),
 (U + "widgets/books/AuditTrail.tsx", "<AccountAudit key={accountId ?? \"\"}", "<AccountAudit key=\"one\""),
 (U + "widgets/research/Backtests.tsx", "const day = chosen && chosen.run === run.run_id ? chosen.day : null;", "const day = chosen ? chosen.day : null;"),
 (U + "widgets/research/Backtests.tsx", "const days = useMemo(() => picked.filter((d) => offered.includes(d)), [picked, offered]);", "const days = picked;"),
 (U + "widgets/strategy/StrategyBuilder.tsx", "const mine = result && result.key === key ? result : null;", "const mine = result;"),
 (U + "widgets/strategy/StrategyBuilder.tsx", "const check = checked && checked.key === key ? checked.check : null;", "const check = checked ? checked.check : null;"),
 (U + "widgets/chart/Chart.tsx", "  if (shownCode !== code) {\n    setShownCode(code);\n    setCandles([]);", "  if (shownCode !== code) {\n    setShownCode(code);\n    void 0;"),
 (U + "widgets/ladder/Ladder.tsx", "  if (selected !== null && !working.some((o) => priceOf(o) === selected)) setSelected(null);", ""),
 (U + "widgets/market/TimeAndSales.tsx", "prints?after=${after}&limit=300", "prints?after=0&limit=300"),
 (U + "workspace/Panel.tsx", "return seen.api === api ? seen.title : (api.title ?? \"\");", "return seen.title;"),
 (U + "workspace/FreeCanvas.tsx", "  if (maximized && !windows.some((w) => w.id === maximized)) setMaximized(null);\n", ""),
 (U + "components/ContextMenu.tsx", "    close.current = onClose; // the newest", "    // the newest"),
]
if subprocess.run(TESTS, cwd="ui", capture_output=True, timeout=900).returncode != 0:
    raise SystemExit("the tests fail before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, cwd="ui", capture_output=True, timeout=900)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40], flush=True)
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
