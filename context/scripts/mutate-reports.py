"""#215 mutation check: break one report rule, tests/test_reports.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files)."""
import subprocess
D, H, X = "src/mocktrade/reports/daily.py", "src/mocktrade/reports/history.py", "src/mocktrade/reports/export.py"
P, S, E = "src/mocktrade/reports/pdf.py", "src/mocktrade/reports/store.py", "src/mocktrade/store/events.py"
V = "src/mocktrade/reports/service.py"
M = [
 (D, "    before = [e for e in fills if e.ts < start]", "    before = [e for e in fills if e.ts <= start and False]"),
 (D, "        reference = tick.close_paise if tick and tick.close_paise else None", "        reference = tick.ltp_paise if tick else None"),
 (D, "        if qty <= 0:\n            continue\n        counted", "        if False:\n            continue\n        counted"),
 (D, "    if request.basket_id:\n        return f\"basket {request.basket_id}\"", "    if False:\n        return f\"basket {request.basket_id}\""),
 (D, "            marks[p.instrument_id] = tick.ltp_paise", "            pass"),
 (D, "        elif p.qty:\n            priced = False", "        elif p.qty:\n            pass"),
 (D, "    views = [v for v in log.latest_views(account_id, since=start) if v.created_at < end]", "    views = list(log.latest_views(account_id, since=start))"),
 (D, "        elif e.kind.startswith(ALERTS):", "        elif True:"),
 (D, "    return tuple(p for p in self.positions if p.closing_qty)", "    return self.positions"),
 (D, "        return self.gross_paise - self.charges_paise\n\n    @property\n    def carried_forward", "        return self.gross_paise\n\n    @property\n    def carried_forward"),
 (H, "    out = [{\"key\": k, **v, \"net_paise\": v[\"gross_paise\"] - v[\"charges_paise\"]}", "    out = [{\"key\": k, **v, \"net_paise\": v[\"gross_paise\"]}"),
 (H, "        \"missing_days\": [d.isoformat() for d in days if d.isoformat() not in kept],", "        \"missing_days\": [],"),
 (X, "    whole, part = divmod(abs(paise), 100)", "    whole, part = divmod(paise, 100)"),
 (P, "    return raw.replace(b\"\\\\\", b\"\\\\\\\\\").replace(b\"(\", b\"\\\\(\").replace(b\")\", b\"\\\\)\")", "    return raw"),
 (P, "    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]", "    pages = [lines]"),
 (S, "            if first <= day <= last and (r := self.load(account_id, day)) is not None:", "            if (r := self.load(account_id, day)) is not None:"),
 (S, "        if not SAFE.fullmatch(account_id):", "        if False:"),
 (E, "        return [e for e in self._query(sql, (*names, low, high)) if start <= e.ts < end]", "        return self._query(sql, (*names, low, high))"),
 (V, "        if day > self.today():", "        if False:"),
 (V, "        if self.store is not None and (kept := self.store.load(account_id, day)) is not None:", "        if False:"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_reports.py"],
                           capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
