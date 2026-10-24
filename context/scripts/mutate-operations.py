"""#287 mutation check: break one rule of operations (log lines carry the ids bound while
they were logged, a caller's request id only when plain, the order manager binding its ids,
errors kept, one status of every part: what is down, starting, degraded, the alert rows, since
when, the background look; readiness from it; the status page for admin and risk of the same
firm; the monitoring key for the Prometheus text only; one TYPE line a metric; the Status tab);
the tests must notice. Restores each file in `finally`; a hang counts as caught. Run alone
from the repo root."""
import subprocess

L = "src/mocktrade/ops/logs.py"
S = "src/mocktrade/ops/status.py"
A = "src/mocktrade/server/app.py"
O = "src/mocktrade/orders/manager.py"
M = "src/mocktrade/metrics.py"
V = "src/mocktrade/tools/serve.py"
U = "ui/src/widgets/admin/Admin.tsx"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_operations.py", "tests/test_metrics.py",
       "tests/test_supervisor.py", "tests/test_workspaces.py"], ".")
UI = (["npx", "vitest", "run", "src/widgets/admin"], "ui")
MUTANTS = [
 (L, "    added = {k: str(v) for k, v in more.items() if v is not None}", "    added = {k: str(v) for k, v in more.items()}", PY),
 (L, "    rid = request_id if request_id and _PLAIN_ID.fullmatch(request_id) else uuid.uuid4().hex[:16]", "    rid = request_id or uuid.uuid4().hex[:16]", PY),
 (L, "    if current is not None:\n        current.update(more)", "    if current is not None:\n        pass", PY),
 (L, "        line.update(_context.get() or {})\n", "", PY),
 (L, "            if value is not None:\n                line[key] = str(value)", "            if value is not None:\n                pass", PY),
 (L, "        if record.exc_text:\n            line[\"error\"] = record.exc_text", "        if record.exc_text:\n            pass", PY),
 (S, "DOWN_WHEN_ERROR = (\"prices\", \"disk\")", "DOWN_WHEN_ERROR = (\"disk\",)", PY),
 (S, "alert.level == \"ERROR\" and alert.account_id is None", "alert.level == \"ERROR\"", PY),
 (S, "    return \"down\" if critical else \"degraded\"  # restarting or quarantined", "    return \"degraded\"  # restarting or quarantined", PY),
 (S, "                state = {\"ok\": \"ready\", \"recovering\": \"starting\"}.get(gate.state, \"down\")", "                state = {\"ok\": \"ready\", \"recovering\": \"ready\"}.get(gate.state, \"down\")", PY),
 (S, "        parts.append(self._timed(\"audit log\", \"down\" if problem else \"ready\",", "        parts.append(self._timed(\"audit log\", \"degraded\" if problem else \"ready\",", PY),
 (S, "NOT_READY = (\"starting\", \"down\")", "NOT_READY = (\"down\",)", PY),
 (S, "            if family in OWN_ROWS:\n                continue", "            if family in OWN_ROWS:\n                pass", PY),
 (S, "        if seen is None or seen[0] != state:", "        if True:", PY),
 (S, "        while True:\n            self.parts()\n", "        while True:\n", PY),
 (S, "            if not any(_family(a) == family for a in active):", "            if True:", PY),
 (A, "        if ok:\n            return {\"ready\": True}", "        if True:\n            return {\"ready\": True}", PY),
 (A, "        parts = [p for p in every if p.account_id is None or in_workspace(user, p.account_id)]", "        parts = every", PY),
 (A, "        if not _risk_or_admin(user):\n            raise HTTPException(403, \"the status page is for admin and risk users\")", "        if False:\n            raise HTTPException(403, \"the status page is for admin and risk users\")", PY),
 (A, "        return bool(token) and hmac.compare_digest(given.encode(), f\"Bearer {token}\".encode())", "        return hmac.compare_digest(given.encode(), f\"Bearer {token}\".encode())", PY),
 (A, "        return bool(token) and hmac.compare_digest(given.encode(), f\"Bearer {token}\".encode())", "        return bool(token) and given.startswith(\"Bearer \")", PY),
 (A, "        if not (format == \"prometheus\" and scraper(request)):", "        if not scraper(request):", PY),
 (A, "        response.headers[\"X-Request-ID\"] = request_id", "        pass", PY),
 (A, "            METRICS.inc(f\"http_responses{{status={response.status_code // 100}xx}}\")", "            pass", PY),
 (O, "            logs.note(client_order_id=request.client_order_id)  # this order's lines from here", "            pass", PY),
 (O, "    @_logged\n    async def submit(", "    async def submit(", PY),
 (M, "            if metric not in typed:\n                typed.add(metric)\n                lines.append", "            if True:\n                typed.add(metric)\n                lines.append", PY),
 (V, "        if log_format not in logs.FORMATS:", "        if False:", PY),
 (V, "        if metrics_token and len(metrics_token) < MIN_METRICS_TOKEN:", "        if False:", PY),
 (U, "    const timer = setInterval(load, REFRESH_MS);", "    const timer = setInterval(load, REFRESH_MS * 10);", UI),
 (U, "degraded: \"tone-warn\"", "degraded: \"tone-pending\"", UI),
 (U, "        {others > 0 && <span className=\"dim\">({others} of another firm's parts not ready)</span>}", "", UI),
 (U, "      {tab === \"status\" && <Status />}", "", UI),
]
for cmd, cwd in (PY, UI):
    if subprocess.run(cmd, capture_output=True, timeout=900, cwd=cwd).returncode != 0:
        raise SystemExit(f"the tests fail before any mutation ({' '.join(cmd)}): fix that first")
caught = 0
for f, a, b, (cmd, cwd) in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
