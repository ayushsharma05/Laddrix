"""#367 on a real demo, in a fresh throwaway folder (port 8767). Run from the repo root:

    python3 context/scripts/drill-data.py      # PASS/FAIL per check; exit 1 if any fail

1. start a demo; log in; place and cancel orders; a failed login; a webhook with a secret path
2. a backup while it runs: its manifest records the audit trail's head
3. audit verify: whole, and matching the backup's head
4. an edit to one past row (as root could do): verify names the row; recomputing every
   chain hides it from verify alone, but not from the backup's head
5. the server's whole log (access log included) holds no password, code, cookie or secret
"""

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from laddrix.server import security  # noqa: E402
from laddrix.store.events import link  # noqa: E402

PORT = 8767
BASE, ORIGIN = f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"
HOME = Path(tempfile.mkdtemp(prefix="laddrix-data-"))
DATA = HOME / "data" / "demo"
ENV = os.environ | {"PYTHONPATH": str(REPO / "src")}
HOOK = "https://hooks.example.com/services/T1/B2/drillSecretPath77"
results: list[bool] = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def py(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=HOME, env=ENV, capture_output=True,
                          text=True)  # fmt: skip


def main():
    log = open(HOME / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "laddrix.tools.demo", "--port", str(PORT),
                             "--ui", str(REPO / "ui" / "dist")], cwd=HOME, env=ENV,
                            stdout=log, stderr=log)  # fmt: skip
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        totp = (DATA / "demo_totp.txt").read_text().strip()
        c = httpx.Client(base_url=BASE, headers={"origin": ORIGIN}, timeout=10)
        bad = c.post("/api/session", json={"username": "demo", "password": "wrong-password-xyz",
                                           "code": "000000"})  # fmt: skip
        window = int(time.time()) // 30
        while int(time.time()) // 30 == window:
            time.sleep(0.5)
        code = security.totp_code(totp, time.time())
        c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                     "code": code}).raise_for_status()  # fmt: skip
        cookie = c.cookies["mt_session"]
        c.get("/kite/callback?request_token=drillRequestToken9&state=drillState8&status=success")
        order = {"instrument_id": "NFO:NIFTY26OCTFUT", "side": "BUY", "qty": 75,
                 "order_type": "LIMIT", "product": "NRML", "price_paise": 2_000_000}  # fmt: skip
        placed = [c.post("/api/accounts/acc-paper/orders", json=order).json() for _ in range(3)]
        for p in placed:
            c.delete(f"/api/accounts/acc-paper/orders/{p['request']['client_order_id']}")
        c.put("/api/alerts/webhook", json={"url": HOOK})
        check(bad.status_code == 401 and len(placed) == 3, "1. logged in, 3 orders placed and "
              "cancelled, a failed login, a webhook set")  # fmt: skip
        time.sleep(1.5)

        backups = HOME / "backups"
        b = py("laddrix.tools.backup", "--data", str(DATA), "--to", str(backups))
        manifest = sorted(backups.glob("2*"))[-1] / "manifest.json"
        import json

        head = json.loads(manifest.read_text())["audit_chain"]
        check(b.returncode == 0 and head["rows"] > 0 and head["broken_at"] is None,
              f"2. a backup while running records the head (row {head['head_seq']} of "
              f"{head['rows']})")  # fmt: skip
        v = py("laddrix.tools.audit", "verify", "--data", str(DATA), "--against", str(manifest))
        check(v.returncode == 0 and "matches the head" in v.stdout, "3. verify: whole, matches")
    finally:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=30)

    events = DATA / "events.db"
    with sqlite3.connect(events) as db:
        db.execute("UPDATE events SET detail = 'nothing to see' WHERE seq = 2")
    v = py("laddrix.tools.audit", "verify", "--data", str(DATA))
    check(v.returncode == 1 and "BROKEN at row 2" in v.stderr, "4. an edited row is named: "
          + v.stderr.strip())  # fmt: skip
    with sqlite3.connect(events) as db:  # the edit hidden: every chain recomputed
        previous = ""
        for seq, *row in db.execute("SELECT seq, ts, account_id, kind, client_order_id, detail, "
                                    "payload FROM events ORDER BY seq").fetchall():  # fmt: skip
            previous = link(previous, tuple(row))
            db.execute("UPDATE events SET chain = ? WHERE seq = ?", (previous, seq))
    alone = py("laddrix.tools.audit", "verify", "--data", str(DATA))
    against = py("laddrix.tools.audit", "verify", "--data", str(DATA), "--against",
                 str(manifest))  # fmt: skip
    check(alone.returncode == 0 and against.returncode == 1 and "rewritten" in against.stderr,
          "4. a recomputed chain passes alone, fails against the backup: "
          + against.stderr.strip())  # fmt: skip

    text = (HOME / "server.log").read_text()
    # (the demo prints its own published password at start: not a secret, not checked)
    leaked = [s for s in ("wrong-password-xyz", code, cookie, totp,
                          "drillRequestToken9", "drillState8", "drillSecretPath77")
              if s in text]  # fmt: skip
    lines = len(text.splitlines())
    check(not leaked and lines > 10, f"5. the server log ({lines} lines) holds no secret"
          + (f": LEAKED {[s[:4] for s in leaked]}" if leaked else ""))  # fmt: skip
    check("Logging error" not in text and "request_token=[redacted]" in text,
          "5. the access log still writes every line, the callback's token hidden")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
