#!/usr/bin/env bash
# Real check of #261 on the demo (paper broker, live simulated prices), from the repo root, with
# no demo running. One MIS position of 150 NIFTY futures has three other things that would
# close it: a resting SELL limit of 75 (the trader's target), a trailing stop algo on the same
# position, and an NRML stop algo on another position. Square-off must: stop the MIS algo (not
# the NRML one), cancel the target, close 150 exactly, and leave the account flat, never short.
set -u
OUT=${OUT:-$(mktemp -d)}
stop() { pkill -f "^python3? -m mocktrade[.]tools[.]demo" 2>/dev/null; sleep 1; }
stop; rm -rf data/demo
python3 -m mocktrade.tools.demo > "$OUT/demo.log" 2>&1 &
sleep 10
python3 - <<'PY'
import subprocess, time, httpx
code = subprocess.run(["python3", "-m", "mocktrade.tools.demo", "--code"],
                      capture_output=True, text=True).stdout.strip()
c = httpx.Client(base_url="http://127.0.0.1:8000", headers={"origin": "http://localhost:8000"})
r = c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code})
print("login:", r.status_code)
A = "/api/accounts/acc-paper"
fut = next(i for i in c.get("/api/instruments", params={"q": "NIFTY"}).json()
           if i["id"].endswith("FUT") and i["id"].startswith("NFO:NIFTY"))["id"]
def net(product):
    rows = c.get(f"{A}/positions").json()["positions"]
    return sum(p["qty"] for p in rows if p["instrument_id"] == fut and p["product"] == product)
buy = c.post(f"{A}/orders", json={"instrument_id": fut, "side": "BUY", "qty": 150,
                                 "order_type": "MARKET", "product": "MIS",
                                 "market_protection": "-1"})
print("buy 150 MIS:", buy.status_code, buy.json().get("status"))
for _ in range(50):
    if net("MIS") == 150: break
    time.sleep(0.1)
print("MIS position:", net("MIS"))
avg = next(p for p in c.get(f"{A}/positions").json()["positions"] if p["product"] == "MIS")
target_price = (int(float(avg["avg_price_paise"])) // 1000 + 20) * 1000  # ~₹200 above: it rests
target = c.post(f"{A}/orders", json={"instrument_id": fut, "side": "SELL", "qty": 75,
                                    "order_type": "LIMIT", "product": "MIS",
                                    "price_paise": target_price}).json()
print("resting target:", target["request"]["client_order_id"], target["status"], target.get("reason", ""))
mis = c.post(f"{A}/algos", json={"code": "TRAILING_STOP", "instrument_id": fut, "side": "SELL",
                                "qty": 150, "product": "MIS",
                                "params": {"trail_paise": 500000}}).json()
nrml = c.post(f"{A}/algos", json={"code": "TRAILING_STOP", "instrument_id": fut, "side": "SELL",
                                 "qty": 75, "product": "NRML",
                                 "params": {"trail_paise": 500000}}).json()
print("algos:", mis.get("algo_id", mis), mis.get("state"), "/", nrml.get("algo_id", nrml), nrml.get("state"))
done = c.post(f"{A}/square-off", json={"confirm": True}).json()
for row in done["closed"]:
    print("square-off:", row["summary"])
lows = []
for _ in range(60):
    lows.append(net("MIS"))
    if lows[-1] == 0: break
    time.sleep(0.1)
time.sleep(1)
algos = {a["algo_id"]: a for a in c.get(f"{A}/algos").json()}
orders = {o["request"]["client_order_id"]: o for o in c.get(f"{A}/orders").json()}
sold = sum(o["filled_qty"] for o in orders.values() if o["request"]["side"] == "SELL"
           and o["request"]["product"] == "MIS")
print("after: MIS position", net("MIS"), "| lowest seen", min(lows), "| MIS sold", sold)
print("target now:", orders[target["request"]["client_order_id"]]["status"])
print("MIS algo:", algos[mis["algo_id"]]["state"], "-", algos[mis["algo_id"]]["reason"])
print("NRML algo:", algos[nrml["algo_id"]]["state"])
closes = [o for o in orders.values() if o["request"].get("reduce_only")]
print("closing orders marked reduce-only:", [(o["request"]["client_order_id"], o["request"]["qty"]) for o in closes])
ok = (net("MIS") == 0 and min(lows) >= 0 and sold == 150
      and orders[target["request"]["client_order_id"]]["status"] == "CANCELLED"
      and algos[mis["algo_id"]]["state"] == "STOPPED"
      and algos[nrml["algo_id"]]["state"] not in ("STOPPED", "DONE", "FAILED"))
print("PASS" if ok else "FAIL")
PY
stop
