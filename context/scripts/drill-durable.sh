#!/usr/bin/env bash
# Real check of #265 on the demo, from the repo root, no demo running: an order is placed and
# the server is killed at once (kill -9: no clean shutdown, like a crash); after a restart the
# order is still known (with its doubt) and the next order gets a new id, never the same one.
set -u
OUT=${OUT:-$(mktemp -d)}
stop() { pkill -9 -f "^python3? -m laddrix[.]tools[.]demo" 2>/dev/null; sleep 1; }
start() { python3 -m laddrix.tools.demo > "$OUT/demo-$1.log" 2>&1 & }
up() { timeout 60 bash -c 'until curl -s localhost:8000/api/health >/dev/null; do sleep 0.5; done'; }
stop; rm -rf data/demo
start 1; up
cat > "$OUT/c.py" <<'PY'
import subprocess, sys, time, httpx
time.sleep(31 - time.time() % 30) if sys.argv[1] != "1" else None
code = subprocess.run(["python3", "-m", "laddrix.tools.demo", "--code"], capture_output=True, text=True).stdout.strip()
c = httpx.Client(base_url="http://127.0.0.1:8000", headers={"origin": "http://localhost:8000"})
assert c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code}).status_code == 200
A = "/api/accounts/acc-paper"
fut = next(i["id"] for i in c.get("/api/instruments", params={"q": "NIFTY"}).json() if i["id"].startswith("NFO:NIFTY") and i["id"].endswith("FUT"))
order = {"instrument_id": fut, "side": "BUY", "qty": 75, "order_type": "MARKET", "product": "NRML", "market_protection": "-1"}
if sys.argv[1] == "1":
    r = c.post(f"{A}/orders", json=order).json()
    print(r["request"]["client_order_id"])
else:
    known = {o["request"]["client_order_id"]: (o["status"], o["pending"]) for o in c.get(f"{A}/orders").json()}
    r = c.post(f"{A}/orders", json={**order, "qty": 150}).json()
    print(repr(known), r["request"]["client_order_id"])
PY
first=$(python3 "$OUT/c.py" 1); stop   # the crash, right after the answer
echo "1. placed $first, then the server was killed (kill -9)"
start 2; up
line=$(python3 "$OUT/c.py" 2)
echo "2. after the restart, orders known: ${line% *}; the next order got: ${line##* }"
stop
if [[ "$line" == *"'$first'"* && "${line##* }" != "$first" ]]; then
  echo "PASS: $first is known after the crash, and its id was not given out again"
else
  echo "FAIL"
fi
