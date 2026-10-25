"""#291 mutation check: break one rule of the surveillance alerts (self-trade: opposite sides
that cross, same account or same user, working orders only; large: past the median after a
day of orders; cancel/modify ratio; bursts near 10 a second, once a minute; once per kind a
day; acknowledge with a note, once; the auditor's export is risk-only); the tests must
notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

S = "src/mocktrade/risk/surveillance.py"
ROUTES_RISK = "src/mocktrade/server/routes/risk.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_surveillance.py"]
MUTANTS = [
 (S, "            if buy is None or sell is None or buy >= sell:", "            if True:"),
 (S, "            if w.account_id != event.account_id and w.user_id != r.user_id:", "            if w.account_id != event.account_id:"),
 (S, "        if not view.is_working:\n            self._working.pop(cid, None)", "        if False:\n            self._working.pop(cid, None)"),
 (S, "        if len(day.sizes) >= lim.large_after:", "        if len(day.sizes) >= 0 and day.sizes:"),
 (S, "            if qty > lim.large_x * usual:", "            if qty > usual:"),
 (S, "        if day.placed >= lim.churn_after and day.churn >= lim.churn_x * day.placed:", "        if day.placed >= lim.churn_after and day.churn >= day.placed:"),
 (S, "        day.recent = [t for t in day.recent if now - t < timedelta(seconds=1)] + [now]", "        day.recent = [*day.recent, now]"),
 (S, "        if last is not None and (every is None or now - last < every):", "        if False:"),
 (S, "        if len(note.strip()) < 5:", "        if False:"),
 (S, "        if alert.acked_by:\n", "        if False:\n"),
 (ROUTES_RISK, "        runtime(account_id, user)\n        _risk_only(user)", "        runtime(account_id, user)"),
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
