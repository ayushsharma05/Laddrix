"""#231: the chart API on a running demo (python -m laddrix.tools.demo): the spec, today's
1-minute and 5-minute candles (recorded history joined to live), one candle checked against the
recorded ticks by hand, the forming candle on the stream, and the marks of a real fill.

    python3 context/scripts/check-charts.py [instrument]
"""
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
from websockets.asyncio.client import connect

from laddrix.charts.candles import IST, MINUTE, bucket_start
from laddrix.research.ticks import EPOCH, FileTickStore

BASE = "http://localhost:8000"
FUT = sys.argv[1] if len(sys.argv) > 1 else "NFO:NIFTY26OCTFUT"


async def main() -> None:
    code = subprocess.check_output(["python3", "-m", "laddrix.tools.demo", "--code"]).decode().strip()
    async with httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}) as c:
        r = await c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code})
        r.raise_for_status()
        spec = (await c.get("/api/charts/spec")).json()
        print("spec:", [i["code"] for i in spec["intervals"]], "| history:", spec["history"])
        one = (await c.get(f"/api/charts/{FUT}/candles", params={"interval": "1m"})).json()
        rows = one["candles"]
        print(f"1m: {len(rows)} candles, live from {one['live_from']}, note {one['note']}")
        for row in rows[-3:]:
            print("   ", row)
        # check one complete minute by hand against the recorded ticks
        store = FileTickStore("data/demo/ticks")
        today = datetime.now(IST).date()
        ticks = [(us, t) for us, t in store.read_timed(today, [FUT])]
        if len(rows) >= 3:
            row = rows[-2]
            start = datetime.fromisoformat(row["t"])
            inside = [t for us, t in ticks
                      if bucket_start(t.exchange_time or t.received_at or EPOCH + timedelta(microseconds=us), MINUTE, "NFO") == start]
            prices = [t.ltp_paise for t in inside]
            print("by hand from the recording:", "O", prices[0], "H", max(prices), "L", min(prices), "C", prices[-1],
                  "| the API:", row["o"], row["h"], row["l"], row["c"],
                  "| match:", (prices[0], max(prices), min(prices), prices[-1]) == (row["o"], row["h"], row["l"], row["c"]))
        five = (await c.get(f"/api/charts/{FUT}/candles", params={"interval": "5m"})).json()
        print(f"5m: {len(five['candles'])} candles; last {five['candles'][-1] if five['candles'] else None}")
        o = await c.post("/api/accounts/acc-paper/orders", json={"instrument_id": FUT, "side": "BUY", "qty": 75,
                         "order_type": "MARKET", "product": "NRML", "market_protection": -1})
        print("order:", o.status_code)
        await asyncio.sleep(1.5)
        marks = (await c.get(f"/api/accounts/acc-paper/charts/{FUT}/marks")).json()["marks"]
        print("marks:", len(marks), "| newest:", marks[-1] if marks else None)
        cookie = f"mt_session={c.cookies['mt_session']}"
        async with connect(BASE.replace("http", "ws") + "/ws", origin=BASE, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"op": "candles", "instrument_id": FUT, "interval": "1m"}))
            seen = []
            async with asyncio.timeout(4):
                try:
                    while len(seen) < 3:
                        m = json.loads(await ws.recv())
                        if m["type"] == "candle":
                            seen.append((datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3], m["candle"]))
                except TimeoutError:
                    pass
            for when, candle in seen:
                print("stream", when, candle)


asyncio.run(main())
