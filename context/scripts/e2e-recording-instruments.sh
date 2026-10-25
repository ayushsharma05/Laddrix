#!/usr/bin/env bash
# Real check of V1.1 (#327) on the demo, from the repo root, with no demo running:
#   1. the demo records for a few seconds: the day's folder gets instruments.json;
#   2. a recording of an expired, non-demo contract (BANKNIFTY25JANFUT, 20 Jan 2025) is made
#      with its details, then replayed with the real replay command: it starts, the contract is
#      known, and a paper order on it is accepted with the day's lot size (30) and refused off it;
#   3. the same day without its list: the replay refuses to start and says why.
set -u
OUT=${OUT:-$(mktemp -d)}
stop() { pkill -f "^python3? -m laddrix.tools.demo" 2>/dev/null; sleep 1; }
stop; rm -rf data/demo data/replay/2025-01-20
python3 -m laddrix.tools.demo > "$OUT/demo.log" 2>&1 &
sleep 12; stop
today=$(ls data/demo/ticks | tail -1)
echo "1. demo recorded $today: $(python3 -c "import json;print(len(json.load(open('data/demo/ticks/$today/instruments.json'))),'instruments described,',len(json.load(open('data/demo/ticks/$today/index.json'))),'recorded')")"
python3 - "$OUT/ticks" <<'PY'
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.research.ticks import FileTickStore
bank = Instrument(Exchange.NFO, "BANKNIFTY25JANFUT", InstrumentKind.FUTURE, 20, 30,
                  date(2025, 1, 30), None, "BANKNIFTY")
t0 = datetime(2025, 1, 20, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
store = FileTickStore(sys.argv[1], describe={bank.id: bank}.get)
rows = []
for n in range(600):  # ten minutes, a tick a second
    p = 4_900_000 + 20 * ((n * 7) % 11 - 5)
    rows.append((Tick(bank.id, p, bids=(DepthLevel(p - 20, 300, 3),),
                      asks=(DepthLevel(p + 20, 300, 3),)), t0 + timedelta(seconds=n)))
store.append(rows)
PY
python3 -m laddrix.tools.demo --replay 2025-01-20 --ticks "$OUT/ticks" --speed 1 --port 8001 > "$OUT/replay.log" 2>&1 &
sleep 8
python3 - <<'PY'
import subprocess, httpx
code = subprocess.run(["python3", "-m", "laddrix.tools.demo", "--replay", "2025-01-20", "--code"],
                      capture_output=True, text=True).stdout.strip()
c = httpx.Client(base_url="http://127.0.0.1:8001", headers={"origin": "http://localhost:8001"})
r = c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code})
print("2. replay login:", r.status_code)
found = c.get("/api/instruments", params={"q": "BANKNIFTY25"}).json()
print("   instrument found:", [(i["id"], i["lot_size"], i["tick_paise"]) for i in found][:3])
order = {"instrument_id": "NFO:BANKNIFTY25JANFUT", "side": "BUY", "order_type": "LIMIT",
         "product": "NRML", "price_paise": 4_899_000}
ok = c.post("/api/accounts/acc-paper/orders", json={**order, "qty": 60})
print("   buy 60 (two lots):", ok.status_code, ok.json().get("status", ok.json()))
bad = c.post("/api/accounts/acc-paper/orders", json={**order, "qty": 45})
print("   buy 45:", bad.status_code, bad.json().get("status", bad.json()))
import time; time.sleep(4)
orders = c.get("/api/accounts/acc-paper/orders").json()
for o in orders:
    o = {k: v for k, v in o.items() if k in ("id", "qty", "status", "filled_qty", "reason", "message")}
    print("  ", o)
PY
stop
rm "$OUT/ticks/2025-01-20/instruments.json"; rm -rf data/replay/2025-01-20
python3 -m laddrix.tools.demo --replay 2025-01-20 --ticks "$OUT/ticks" --port 8001 > "$OUT/refused.log" 2>&1
echo "3. without its list: exit $? -> $(tail -1 "$OUT/refused.log")"
