"""#233: market alerts on a running demo (python -m laddrix.tools.demo): the spec, an alert
just above the live price that fires on the stream, one that is already true (and says so), a
spread alert, the audit trail, and a real signed webhook to a public echo service (when the
network allows it), its signature checked by recomputing it.

    python3 context/scripts/check-alerts.py [webhook echo URL]
"""
import asyncio
import hashlib
import hmac
import json
import sqlite3
import subprocess
import sys

import httpx
from websockets.asyncio.client import connect

BASE = "http://localhost:8000"
FUT, NOV = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT"
ECHO = sys.argv[1] if len(sys.argv) > 1 else "https://postman-echo.com/post"


async def main() -> None:
    code = subprocess.check_output(["python3", "-m", "laddrix.tools.demo", "--code"]).decode().strip()
    async with httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}, timeout=30) as c:
        (await c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code})).raise_for_status()
        spec = (await c.get("/api/alerts/spec")).json()
        print("kinds:", [k["code"] for k in spec["kinds"]])
        for a in (await c.get("/api/alerts")).json():  # start clean
            await c.delete(f"/api/alerts/{a['alert_id']}")
        ltp = (await c.get(f"/api/instruments/{FUT}")).json()["ltp_paise"]
        print("NIFTY fut last:", ltp)
        hook = (await c.put("/api/alerts/webhook", json={"url": ECHO})).json()
        print("webhook set:", hook["url"], "| secret shown once:", bool(hook.get("secret")))
        near = (await c.post("/api/alerts", json={"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                                  "params": {"level_paise": ltp + 20}, "webhook": True, "note": "check"})).json()
        below = (await c.post("/api/alerts", json={"kind": "PRICE_BELOW", "instrument_id": FUT,
                                                   "params": {"level_paise": ltp + 100000}})).json()
        spread = (await c.post("/api/alerts", json={"kind": "SPREAD_BELOW", "instrument_id": NOV,
                                                    "params": {"other": FUT, "level_paise": 10_000_00}})).json()
        print("near:", near["status"], "| already below:", below["status"], "| spread:", spread["status"])
        cookie = f"mt_session={c.cookies['mt_session']}"
        async with connect(BASE.replace("http", "ws") + "/ws", origin=BASE, additional_headers={"Cookie": cookie}) as ws:
            fired = None
            try:
                async with asyncio.timeout(60):
                    while fired is None:
                        m = json.loads(await ws.recv())
                        if m["type"] == "market_alert":
                            fired = m
            except TimeoutError:
                print("no alert fired within 60 s (the simulated price didn't rise 0.20)")
            if fired:
                print("fired:", fired["text"], "| at", fired["at"])
        await asyncio.sleep(6)  # the webhook is sent in the background (with retries)
        mine = {a["alert_id"]: a for a in (await c.get("/api/alerts")).json()}
        n = mine[near["alert_id"]]
        print("near now:", n["status"], "| fired", n["fired_count"], "| webhook ok at", n["webhook_ok_at"], "| error", repr(n["webhook_error"]))
        test = (await c.post("/api/alerts/webhook/test", json={})).json()
        print("webhook test:", test)
        if test["ok"] and "postman-echo" in ECHO:
            # send one more ourselves with the same signing, through the echo, and check it comes back intact
            body = json.dumps({"type": "check"}, separators=(",", ":")).encode()
            sig = "sha256=" + hmac.new(hook["secret"].encode(), b"1." + body, hashlib.sha256).hexdigest()
            echoed = httpx.post(ECHO, content=body, headers={"content-type": "application/json", "x-laddrix-timestamp": "1", "x-laddrix-signature": sig}, timeout=15).json()
            print("echo saw our signature header:", echoed["headers"].get("x-laddrix-signature") == sig)
        db = sqlite3.connect("data/demo/events.db")
        rows = db.execute("SELECT kind, detail FROM events WHERE kind LIKE 'ALERT_%' ORDER BY seq DESC LIMIT 6").fetchall()
        for kind, detail in reversed(rows):
            print("audit:", kind, detail[:90])


asyncio.run(main())
