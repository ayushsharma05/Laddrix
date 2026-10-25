"""#396 mutation check: break one rule of "files and jobs per firm and per subscription" (candles
in a folder per firm, a backtest reading its owner's own days and candles, recordings per Kite
subscription, an admin deleting only their own subscription's days, reports marked at the
account's own prices, each subscription checked for silence on its own, dropped ticks counted on
every subscription); the tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

B = "src/laddrix/research/backtest.py"
A = "src/laddrix/server/app.py"
ROUTES_RESEARCH = "src/laddrix/server/routes/research.py"
R = "src/laddrix/reports/service.py"
C = "src/laddrix/ops/checks.py"
V = "src/laddrix/tools/serve.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_firm_files.py"]
MUTANTS = [
 (B, "        return CandleStore(self.root / f\"@{workspace}\")", "        return CandleStore(self.root)"),
 (B, "        if workspace == MAIN:\n            return CandleStore(self.root)", "        if workspace:\n            return CandleStore(self.root)"),
 (B, "        if not workspace.isascii() or not workspace.replace(\"-\", \"\").isalnum():\n            raise BacktestError", "        if False:\n            raise BacktestError"),
 (B, "        ticks, candles = self._sources(run.owner)", "        ticks, candles = self._sources(\"ravi\")"),
 (B, "        if candles and self._sources(owner)[1] is None:", "        if False:"),
 (ROUTES_RESEARCH, "        return candles_for(user.workspace) if candles_for is not None else candles", "        return candles_for(MAIN) if candles_for is not None else candles"),
 (ROUTES_RESEARCH, "        if candles_for is None:  # one dataset for every firm on the server\n            platform_wide", "        if False:\n            platform_wide"),
 (ROUTES_RESEARCH, "        if not platform.prices.per_account:  # Laddrix's own recordings: every firm's\n            platform_wide", "        if True:\n            platform_wide"),
 (ROUTES_RESEARCH, "        found = market(user).ticks\n", "        found = next(iter(filter(None, (markets.of(s).ticks for s in platform.prices.all()))), None)\n"),
 (ROUTES_RESEARCH, "        store = market(user).ticks  # their own subscription's days (#396)", "        store = markets.of(platform.prices.all()[0]).ticks"),
 (A, "        ticks_for=ticks_for or (lambda md: ticks if md is platform.market_data else None),", "        ticks_for=lambda md: ticks,"),
 (R, "            return self._platform.prices.of(account_id).latest", "            return self._platform.market_data.latest"),
 (R, "        except LookupError:\n            return None", "        except LookupError:\n            return self._platform.market_data.latest"),
 (C, "        for feed in self._feeds:\n            self._check_feed(feed)", "        for feed in self._feeds[:1]:\n            self._check_feed(feed)"),
 (C, "                account_id=feed.account_id,  # one firm's subscription: its viewers' band", "                account_id=None,"),
 (C, "        return \"prices\" if self.account_id is None else f\"prices:{self.account_id}\"", "        return \"prices\""),
 (C, "        if not feed.expected():", "        if False:"),
 (C, "        return sum(md.dropped for md in self._sources)", "        return self._sources[0].dropped"),
 (V, "                   account.id)  # fmt: skip", "                   account.id) if account.id == 'acc-a' else None  # fmt: skip"),
 (V, "            record(prices.of(account.id), settings.data_dir / \"ticks\" / f\"@{account.id}\",", "            record(md, settings.data_dir / \"ticks\" / f\"@{account.id}\","),
 (V, "    if settings.record_days > 0 and kite is None:", "    if settings.record_days > 0:"),
 (V, "        source = prices.for_user(user, users.accounts())", "        source = prices.of(\"acc-a\")"),
 (V, "        return days, candle_stores.of(user.workspace).rows", "        return days, candle_stores.of(\"main\").rows"),
 (V, "        if user is None:\n            return (lambda _day: None), None", "        if False:\n            return (lambda _day: None), None"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
