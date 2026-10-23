"""#290: F&O expiry day on a running demo whose near contracts expire today:

    MOCKTRADE_SIM_NEAR_EXPIRY=$(TZ=Asia/Kolkata date +%F) python3 -m mocktrade.tools.demo
    python3 context/scripts/check-expiry.py          # before 15:30 India time
    python3 context/scripts/check-expiry.py --after  # after 15:30

Before: the instrument and its order spec say "Expires today", the chain says it too, an
order goes through. After: search leaves it out, its detail page still answers, an order in it
(even a close) is refused with the reason, the chain no longer lists its expiry.
"""
import asyncio
import subprocess
import sys

import httpx

BASE = "http://localhost:8000"
FUT = "NFO:NIFTY26OCTFUT"


def demo_code() -> str:
    return subprocess.check_output(["python3", "-m", "mocktrade.tools.demo", "--code"]).decode().strip()


async def main(after: bool) -> None:
    c = httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}, timeout=30)
    r = await c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": demo_code()})
    r.raise_for_status()
    acc = (await c.get("/api/me")).json()["accounts"][0]["id"]
    inst = (await c.get(f"/api/instruments/{FUT}")).json()
    print("instrument expiry_info:", inst["expiry_info"])
    spec = (await c.get(f"/api/accounts/{acc}/instruments/{FUT}/order-spec")).json()
    print("order spec expiry note:", spec["expiry"]["note"])
    found = [i["symbol"] for i in (await c.get("/api/instruments", params={"q": "NIFTY26"})).json()]
    print("search NIFTY26:", found)
    print("chain expiries:", (await c.get("/api/options/NIFTY/expiries")).json())
    order = {"instrument_id": FUT, "side": "BUY", "qty": inst["lot_size"], "order_type": "MARKET", "product": "NRML", "market_protection": -1}
    placed = (await c.post(f"/api/accounts/{acc}/orders", json=order)).json()
    print("a market buy:", placed["status"], placed.get("reason", ""))
    await c.aclose()


asyncio.run(main("--after" in sys.argv))
