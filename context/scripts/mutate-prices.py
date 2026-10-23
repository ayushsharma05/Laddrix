"""#395 (first half) mutation check: break one rule of "each trader's own prices" (the source
rules, each account checked and marked on its own feed, each Kite account's ticker feeding only
its own prices, a paper account on its owner's, each screen on its owner's subscription); the
tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

S = "src/mocktrade/marketdata/sources.py"
P = "src/mocktrade/platform.py"
K = "src/mocktrade/broker/kite/live.py"
V = "src/mocktrade/tools/serve.py"
H = "src/mocktrade/server/stream.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_price_sources.py"]
MUTANTS = [
 (S, "        if self.per_account:  # a Kite server never falls back to someone else's prices\n            raise LookupError", "        if False:\n            raise LookupError"),
 (S, "            if (account.owner_user_id == user.id and account.workspace == user.workspace", "            if (account.workspace == user.workspace"),
 (S, "        if not self.per_account:\n            return self.shared\n        for account", "        if True:\n            return self.shared\n        for account"),
 (S, "        source = self._uses.get(account_id, account_id)", "        source = account_id"),
 (P, "        md = self.prices.of(account.id)  # its own subscription's prices, no one else's (#395)", "        md = self.market_data"),
 (P, "                        for runtime in by_source[waits[task]][1]:", "                        for runtime in self.accounts.values():"),
 (K, "        link.feed.on_ticks(ticks)  # its own prices, always (#395)", "        self._feed.on_ticks(ticks)"),
 (K, "        prices = MarketData()\n", "        prices = self._md\n"),
 (V, "                prices.borrow(account.id, source)\n", ""),
 (V, "                if source is None:", "                if False:"),
 (H, "            ticks=self._prices_for(user).subscribe(),", "            ticks=self._platform.market_data.subscribe(),"),
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
