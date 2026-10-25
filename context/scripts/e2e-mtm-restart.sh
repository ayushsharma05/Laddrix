#!/usr/bin/env bash
# Real check of #263 on the demo (paper, recover on start), from the repo root, no demo running:
#   1. an MIS position with an auto-close loss limit of ₹1: it fires, closes, and the breach is
#      COMPLETED (closed, and nothing in doubt);
#   2. the demo restarts: the breach and "fired today" come back from disk, nothing is sent
#      again, even though the limit is still past on the next prices;
#   3. the rules file is damaged and the demo restarts again: the limits screen and the ops
#      band say no loss limits are watched, and the damaged file is kept aside.
set -u
OUT=${OUT:-$(mktemp -d)}
stop() { pkill -f "^python3? -m laddrix[.]tools[.]demo" 2>/dev/null; sleep 2; }
start() { python3 -m laddrix.tools.demo > "$OUT/demo-$1.log" 2>&1 & sleep 12; }
stop; rm -rf data/demo
start 1
cat > "$OUT/check.py" <<'PY'
import subprocess, sys, time, httpx
step = sys.argv[1]
if step != "1":
    time.sleep(31 - time.time() % 30)  # a fresh code: each one works only once
code = subprocess.run(["python3", "-m", "laddrix.tools.demo", "--code"],
                      capture_output=True, text=True).stdout.strip()
c = httpx.Client(base_url="http://127.0.0.1:8000", headers={"origin": "http://localhost:8000"})
assert c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                     "code": code}).status_code == 200
A = "/api/accounts/acc-paper"
mtm_orders = lambda: [o for o in c.get(f"{A}/orders").json()
                      if (o["request"].get("basket_id") or "").startswith("MTM")]
if step == "1":
    fut = next(i["id"] for i in c.get("/api/instruments", params={"q": "NIFTY"}).json()
               if i["id"].startswith("NFO:NIFTY") and i["id"].endswith("FUT"))
    c.post(f"{A}/orders", json={"instrument_id": fut, "side": "BUY", "qty": 150,
                                "order_type": "MARKET", "product": "MIS", "market_protection": "-1"})
    time.sleep(1.5)
    r = c.put(f"{A}/mtm/positions", json={"instrument_id": fut, "product": "MIS",
                                          "max_loss_paise": 100, "auto_close": True})
    print("1. rule set:", r.status_code)
    time.sleep(4)
for _ in range(20):
    body = c.get(f"{A}/mtm").json()
    if body["breaches"] or step == "3":
        break
    time.sleep(0.5)
print(f"{step}. breaches:", [(b["key"].split(":")[-1], b["state"], b["detail"]) for b in body["breaches"]])
print(f"{step}. fired today:", [r["fired_today"] for r in body["positions"]], "| MTM close orders:", len(mtm_orders()))
print(f"{step}. problem:", body["problem"] or "(none)")
if step == "3":
    print("3. ops band:", [a["text"][:110] for a in c.get("/api/ops").json().get("alerts", []) if "MTM" in a["text"]])
PY
python3 "$OUT/check.py" 1
stop; start 2
sleep 4  # prices move past the limit again
python3 "$OUT/check.py" 2
stop
echo '{"positions": [{"instrument_id": "NFO:X", "product"' > data/demo/rules/mtm-acc-paper.json
start 3
python3 "$OUT/check.py" 3
echo "3. kept aside: $(ls data/demo/rules | grep unreadable)"
stop
