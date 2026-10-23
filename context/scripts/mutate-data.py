"""#367 mutation check: break one rule of log redaction, the audit trail's hash chain, the
backup's recorded head, and one person's export and erase; the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

L = "src/mocktrade/logsafe.py"
E = "src/mocktrade/store/events.py"
B = "src/mocktrade/tools/backup.py"
A = "src/mocktrade/tools/audit.py"
U = "src/mocktrade/server/users.py"
T = "src/mocktrade/tools/users.py"
K = "src/mocktrade/broker/kite/session.py"
C = "src/mocktrade/config.py"
N = "src/mocktrade/ops/notify.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_log_redaction.py",
         "tests/test_audit_chain.py", "tests/test_personal_data.py"]
MUTANTS = [
 (L, "    text = _PARAM.sub(lambda m: f\"{m[1]}={HIDDEN}\", text)", "    pass"),
 (L, "    text = _JSON.sub(lambda m: f'{m[1]}\"{HIDDEN}\"', text)", "    pass"),
 (L, "    text = _AUTH.sub(lambda m: f\"{m[1]}{HIDDEN}\", text)", "    pass"),
 (L, "    text = _FERNET.sub(HIDDEN, text)", "    pass"),
 (L, "    return _URL.sub(lambda m: f\"{m[1]}/{HIDDEN}\", text)", "    return text"),
 (L, "            record.exc_text = redact(record.exc_text)", "            pass"),
 (L, "        logging.getLogger(name).setLevel(logging.WARNING)", "        pass"),
 (L, "            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)", "            pass"),
 (L, "        for handler in logger.handlers:", "        for handler in []:"),
 (K, "    access_token: str = field(repr=False)", "    access_token: str"),
 (C, "    api_secret: str = field(repr=False)", "    api_secret: str"),
 (N, "            log.warning(\"alert webhook failed: %s\", type(exc).__name__)", "            log.warning(\"alert webhook failed: %r\", exc)"),
 (E, "            head = link(head, row)", "            head = link(\"\", row)"),
 (E, "            if chain != link(previous, tuple(row)):", "            if False:"),
 (E, "                if previous:", "                if False:"),
 (E, "            self._head = last[0] if last else \"\"", "            self._head = \"\""),
 (B, "            chain = verify_chain(part / \"events.db\")", "            chain = verify_chain(data_dir / \"nothing.db\") if False else verify_chain(part / \"events.db\")._replace() if False else None"),
 (A, "    if found[0] != recorded[\"head\"]:", "    if False:"),
 (A, "    if found is None:", "    if False:"),
 (U, "            for table in (\"sessions\", \"user_settings\", \"layouts\"):", "            for table in (\"sessions\", \"user_settings\"):"),
 (U, "                    db.execute(\"UPDATE accounts SET traders = ? WHERE id = ?\", (left, account.id))", "                    pass"),
 (U, "        if owned:", "        if False:"),
 (T, "    fence = DataFence(data)  # the running server holds alerts in memory: it would write back\n    try:\n        fence.acquire()", "    fence = DataFence(data)\n    try:\n        pass"),
 (T, "            path.unlink()", "            pass"),
 (T, "            if owner == user_id:", "            if True:"),
 (T, "    return user_id in (payload.get(\"by\"), payload.get(\"user_id\"), request.get(\"user_id\"))", "    return user_id == payload.get(\"user_id\")"),
 (T, "    found[\"webhook\"] = {\"url\": hook[\"url\"], \"signing_key\": \"set (not shown)\"} if hook else None", "    found[\"webhook\"] = hook"),
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
