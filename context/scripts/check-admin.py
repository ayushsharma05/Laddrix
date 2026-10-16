"""#281: the admin API on a running demo (python -m mocktrade.tools.demo). As the demo admin:
add a user, who logs in with the one-time password and must set their own first; add a paper
account for them, which runs at once and takes their order; tighten a limit (a preview first,
then for real) that their next order meets; turn the account off (opening refused, cancel
works); the audit rows. Run again with --after-restart once the demo was restarted: the limit
and the account are still there.

    python3 context/scripts/check-admin.py [--after-restart]
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp

BASE = "http://localhost:8000"
FUT = "NFO:NIFTY26OCTFUT"
STATE = Path("/tmp/claude-0/check-admin.json")  # what the restart check looks for


def demo_code() -> str:
    return subprocess.check_output(["python3", "-m", "mocktrade.tools.demo", "--code"]).decode().strip()


async def admin() -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}, timeout=30)
    r = await c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": demo_code()})
    r.raise_for_status()
    return c


async def first_run() -> None:
    c = await admin()
    spec = (await c.get("/api/admin/limits/spec")).json()
    print("limits in the spec:", len(spec["params"]), "| kinds:", sorted({p["kind"] for p in spec["params"]}))
    uid = f"check-{int(time.time()) % 100000}"
    made = (await c.post("/api/admin/users", json={"id": uid, "name": "Check User", "roles": ["trader"]})).json()
    print("new user:", made["user"]["id"], made["user"]["roles"], "| must change:", made["user"]["must_change_password"])
    secret = parse_qs(urlparse(made["totp_uri"]).query)["secret"][0]
    u = httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}, timeout=30)
    login = await u.post("/api/session", json={"username": uid, "password": made["temporary_password"], "code": pyotp.TOTP(secret).now()})
    print("their login:", login.status_code, "| must change:", login.json()["user"]["must_change_password"])
    print("before the change, /api/instruments:", (await u.get("/api/instruments")).status_code)
    new_password = "check user's own password"
    print("password change:", (await u.post("/api/me/password", json={"old": made["temporary_password"], "new": new_password})).status_code)
    print("after, /api/instruments:", (await u.get("/api/instruments")).status_code)

    acc = f"acc-{uid}"
    account = (await c.post("/api/admin/accounts", json={"id": acc, "broker": "paper", "broker_client_id": "CHECK1", "owner": uid})).json()
    print("new account:", account["id"], "| running:", account["running"], account["not_running"])
    await asyncio.sleep(1.5)  # its recovery (nothing to rebuild) finishes in the background
    inst = (await u.get(f"/api/instruments/{FUT}")).json()
    lot, ltp, tick = inst["lot_size"], inst["ltp_paise"], inst["tick_paise"]
    order = {"instrument_id": FUT, "side": "BUY", "qty": lot, "order_type": "LIMIT", "product": "NRML",
             "price_paise": (ltp // tick - 200) * tick}  # well below: it stays working
    first = (await u.post(f"/api/accounts/{acc}/orders", json=order)).json()
    print("their first order:", first["status"], first.get("reason", ""))

    limits = (await c.get(f"/api/admin/accounts/{acc}/limits")).json()["limits"]
    wanted = {**limits, "max_order_lots": 1}
    preview = (await c.put(f"/api/admin/accounts/{acc}/limits?dry_run=true", json={"limits": wanted})).json()
    print("preview:", [x["text"] for x in preview["changes"]], "| applied:", preview["applied"])
    applied = (await c.put(f"/api/admin/accounts/{acc}/limits", json={"limits": wanted})).json()
    print("applied:", applied["applied"], "| looser?", [x["looser"] for x in applied["changes"]])
    big = (await u.post(f"/api/accounts/{acc}/orders", json={**order, "qty": 2 * lot})).json()
    print("a 2-lot order now:", big["status"], "|", big.get("reason"))

    off = (await c.patch(f"/api/admin/accounts/{acc}", json={"active": False})).json()
    print("account turned off:", off["active"] is False)
    opening = (await u.post(f"/api/accounts/{acc}/orders", json=order)).json()
    print("an opening order now:", opening["status"], "|", opening.get("reason"))
    cancel = await u.delete(f"/api/accounts/{acc}/orders/{first['request']['client_order_id']}")
    print("cancel the working one:", cancel.status_code, cancel.json().get("pending"))
    on = (await c.patch(f"/api/admin/accounts/{acc}", json={"active": True})).json()
    print("turned on again:", on["active"])
    self_off = await c.patch("/api/admin/users/demo", json={"active": False})
    print("demo turning itself off:", self_off.status_code, self_off.json()["detail"])

    db = sqlite3.connect("data/demo/events.db")
    rows = db.execute("SELECT kind, detail FROM events WHERE kind LIKE 'ADMIN_%' OR kind = 'PASSWORD_CHANGED' ORDER BY seq DESC LIMIT 7").fetchall()
    print("audit (newest first):")
    for kind, detail in rows:
        print("  ", kind, "|", detail)
    STATE.write_text(json.dumps({"user": uid, "account": acc, "password": new_password, "secret": secret}))
    await u.aclose()
    await c.aclose()


async def after_restart() -> None:
    state = json.loads(STATE.read_text())
    c = await admin()
    acc = state["account"]
    limits = (await c.get(f"/api/admin/accounts/{acc}/limits")).json()
    listed = {a["id"]: a for a in (await c.get("/api/admin/accounts")).json()["accounts"]}
    print("after a restart:", acc, "running:", listed[acc]["running"], "| max lots:", limits["limits"]["max_order_lots"])
    users = {u["id"]: u for u in (await c.get("/api/admin/users")).json()}
    print("their user:", users[state["user"]]["roles"], "| must change:", users[state["user"]]["must_change_password"])
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(after_restart() if "--after-restart" in sys.argv else first_run())
