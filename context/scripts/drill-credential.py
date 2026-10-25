"""Compromised-credential drill on the demo (#364). Runs the playbook in docs/runbooks/incidents.md
("A credential is stolen") for real, in a fresh throwaway folder (your own data/demo is never
touched). Run from the repo root with nothing on port 8765:

    python3 context/scripts/drill-credential.py      # PASS/FAIL per check; exit 1 if any fail

1. start a demo; log in; open a live stream (as a browser tab does)
2. contain: `users revoke-sessions` from another process: the open stream closes within
   seconds, the stolen cookie gets 401; a new login works
3. stop new risk: firm STOP; a new order is refused
4. keep the evidence: a backup is made; it holds no session
5. rotate the key: refused while the server runs; stop it; `--check`; rotate; the new key in
   place; start again: the same authenticator logs in, the old key opens nothing
6. restore without old sessions: the stolen cookie still gets 401 after the restart
"""

import asyncio
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from laddrix.server import security  # noqa: E402
from laddrix.server.security import SecretBox  # noqa: E402

PORT = 8765
BASE, ORIGIN = f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"
results: list[tuple[bool, str]] = []
HOME = Path(tempfile.mkdtemp(prefix="laddrix-drill-"))
DATA = HOME / "data" / "demo"
ENV = os.environ | {"PYTHONPATH": str(REPO / "src")}


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def tool(*args: str, **extra: str) -> subprocess.CompletedProcess:
    env = ENV | {"LADDRIX_SECRET_KEY": (DATA / "secret.key").read_text().strip(),
                 "LADDRIX_USERS_DB": str(DATA / "users.db")} | extra  # fmt: skip
    return subprocess.run([sys.executable, "-m", "laddrix.tools.users", *args], cwd=HOME,
                          env=env, capture_output=True, text=True)  # fmt: skip


def start() -> subprocess.Popen:
    log = open(HOME / "demo.log", "a")
    proc = subprocess.Popen([sys.executable, "-m", "laddrix.tools.demo", "--port", str(PORT),
                             "--ui", str(REPO / "ui" / "dist")],
                            cwd=HOME, env=ENV, stdout=log, stderr=log)  # fmt: skip
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the demo did not start (see demo.log in " + str(HOME) + ")")


def stop(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGINT)  # a clean stop, as `docker compose stop` asks
    proc.wait(timeout=30)


def login() -> httpx.Client:
    window = int(time.time()) // 30
    while int(time.time()) // 30 == window:  # a fresh code (each one works once)
        time.sleep(0.5)
    code = security.totp_code((DATA / "demo_totp.txt").read_text().strip(), time.time())
    c = httpx.Client(base_url=BASE, headers={"origin": ORIGIN}, timeout=10)
    r = c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                     "code": code})  # fmt: skip
    r.raise_for_status()
    return c


async def stream_closes_after_revoke(cookie: str) -> tuple[float, int | None]:
    async with connect(f"ws://127.0.0.1:{PORT}/ws", origin=ORIGIN,
                       additional_headers={"cookie": f"mt_session={cookie}"}) as ws:  # fmt: skip
        while True:  # the hello first: the stream is open
            if '"hello"' in await ws.recv():
                break
        revoked = tool("revoke-sessions")
        print("    " + revoked.stdout.strip(), flush=True)
        began = time.monotonic()
        try:
            while True:
                await asyncio.wait_for(ws.recv(), 10)
        except ConnectionClosed as exc:
            return time.monotonic() - began, exc.rcvd.code if exc.rcvd else None
        except TimeoutError:
            return time.monotonic() - began, None


def main() -> int:
    print(f"drill folder: {HOME}")
    server = start()
    try:
        thief = login()  # the stolen cookie
        cookie = thief.cookies["mt_session"]
        check(thief.get("/api/me").status_code == 200, "1. logged in; the session works")

        took, code = asyncio.run(stream_closes_after_revoke(cookie))
        check(code == 4401 and took < 5,
              f"2. revoke-sessions closed the open stream in {took:.1f} s (code {code})")
        check(thief.get("/api/me").status_code == 401, "2. the stolen cookie now gets 401")
        me = login()
        check(me.get("/api/me").status_code == 200, "2. a new login (password + code) works")

        r = me.post("/api/firm/stop", json={"reason": "drill: credential stolen"})
        check(r.status_code == 200 and r.json()["stopped"], "3. firm STOP")
        order = {"instrument_id": "NFO:NIFTY26OCTFUT", "side": "BUY", "qty": 75,
                 "order_type": "MARKET", "product": "NRML"}  # fmt: skip
        r = me.post("/api/accounts/acc-paper/orders", json=order)
        refused = r.status_code >= 400 or r.json().get("status") == "REJECTED"
        check(refused, f"3. a new order is refused while stopped ({r.status_code})")

        backups = HOME / "backups"
        b = subprocess.run([sys.executable, "-m", "laddrix.tools.backup", "--data", str(DATA),
                            "--to", str(backups)], cwd=HOME, env=ENV, capture_output=True,
                           text=True)  # fmt: skip
        made = sorted(backups.glob("2*"))
        check(b.returncode == 0 and made, "4. evidence: a backup was made while running")
        with sqlite3.connect(made[-1] / "users.db") as db:
            held = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        check(held == 0, "4. the backup holds no session (a restore brings none back)")

        new_key = SecretBox.new_key()
        running = tool("rotate-key", LADDRIX_NEW_SECRET_KEY=new_key)
        check(running.returncode != 0 and "stop the server first" in running.stderr,
              "5. rotate-key refused while the server runs")  # fmt: skip
    finally:
        stop(server)

    old_key = (DATA / "secret.key").read_text().strip()
    dry = tool("rotate-key", "--check", LADDRIX_NEW_SECRET_KEY=new_key)
    check(dry.returncode == 0 and "ready to rotate" in dry.stdout, "5. rotate-key --check: ready")
    done = tool("rotate-key", LADDRIX_NEW_SECRET_KEY=new_key)
    check(done.returncode == 0 and "Re-encrypted 1 secret" in done.stdout, "5. rotated")
    check(new_key not in done.stdout + done.stderr, "5. the key itself is never printed")
    shutil.copy(DATA / "secret.key", HOME / "old.key")  # kept with the old backups
    (DATA / "secret.key").write_text(new_key)  # the demo's .env

    server = start()
    try:
        again = login()
        check(again.get("/api/me").status_code == 200,
              "5. after the restart the same authenticator logs in under the new key")  # fmt: skip
        stale = httpx.Client(base_url=BASE, headers={"origin": ORIGIN},
                             cookies={"mt_session": cookie})  # fmt: skip
        check(stale.get("/api/me").status_code == 401, "6. the stolen cookie still gets 401")
        old = tool("rotate-key", "--check", LADDRIX_SECRET_KEY=old_key,
                   LADDRIX_NEW_SECRET_KEY=SecretBox.new_key())  # fmt: skip
        check(old.returncode != 0 and "can't open the secrets of demo" in old.stderr,
              "5. the old key opens nothing any more")  # fmt: skip
    finally:
        stop(server)

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    shutil.rmtree(HOME, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
