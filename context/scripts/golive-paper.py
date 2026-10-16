"""The go-live checklist's PAPER steps (V1.4, #330), on the demo. Repo root, no demo running:

    python3 context/scripts/golive-paper.py     # PASS/FAIL per step; exit 1 if any fail

Every line it prints is PAPER evidence: the paper broker filling against the demo's simulated
prices. None of it is evidence about Kite. The live steps are in docs/runbooks/go-live.md and
are run by a person, with a live login, and never without Ayush's explicit go-ahead.

    orders     a limit rests, is modified (price and quantity), is cancelled; a market order
               fills; a refused order says why (off the tick grid, a lot that isn't whole)
    positions  the fill shows as a position; the broker check (reconcile) agrees
    protection a stop-loss order (SL-M) rests on the closing side and is cancelled; a GTT is
               placed, listed as active and deleted
    square-off the intraday (MIS) position is closed by the square-off command, after asking
    webhooks   a private or plain-http address is refused (the delivery itself is a live step)
"""

import subprocess
import sys
import time

import httpx

BASE, ORIGIN = "http://127.0.0.1:8000", "http://localhost:8000"
ACC, FUT = "acc-paper", "NFO:NIFTY26OCTFUT"
results: list[tuple[bool, str]] = []


def check(ok, what):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def start():
    subprocess.run(["pkill", "-f", "[-]m mocktrade[.]tools[.]demo"], check=False)
    time.sleep(1)
    subprocess.run(["rm", "-rf", "data/demo"], check=True)
    log = open("data/golive-demo.log", "w")
    subprocess.Popen([sys.executable, "-m", "mocktrade.tools.demo"], stdout=log, stderr=log)
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the demo did not start")


def login():
    code = subprocess.run([sys.executable, "-m", "mocktrade.tools.demo", "--code"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    c = httpx.Client(base_url=BASE, headers={"origin": ORIGIN}, timeout=10)
    c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                 "code": code}).raise_for_status()  # fmt: skip
    return c


def until(fn, seconds=8):
    end = time.time() + seconds
    while True:
        value = fn()
        if value or time.time() > end:
            return value
        time.sleep(0.3)


def main():
    start()
    c = login()
    acc = f"/api/accounts/{ACC}"

    def order(**fields):
        return c.post(f"{acc}/orders", json={"instrument_id": FUT, "product": "NRML", **fields})

    def get(cid):
        return next(o for o in c.get(f"{acc}/orders").json()
                    if o["request"]["client_order_id"] == cid)  # fmt: skip

    def net(product=None):
        pos = c.get(f"{acc}/positions").json()["positions"]
        return sum(p["qty"] for p in pos
                   if p["instrument_id"] == FUT and product in (None, p["product"]))  # fmt: skip

    print("--- orders")
    buy = order(side="BUY", qty=75, order_type="MARKET", market_protection="2")
    check(buy.status_code == 201, "a market buy is accepted")
    check(until(lambda: net() == 75), "and fills: position +75")
    last = until(lambda: get(buy.json()["request"]["client_order_id"])["avg_price_paise"])
    away = int(last * 0.98) // 10 * 10  # 2% under the market: rests
    r = order(side="BUY", qty=75, order_type="LIMIT", price_paise=away)
    cid = r.json()["request"]["client_order_id"]
    check(until(lambda: get(cid)["status"] == "OPEN"), f"a limit buy rests at {away / 100:,.2f}")
    m = c.patch(f"{acc}/orders/{cid}", json={"price_paise": away - 100, "qty": 150})
    check(m.status_code in (200, 202) and until(
        lambda: get(cid)["request"]["price_paise"] == away - 100 and get(cid)["request"]["qty"] == 150),
        f"modified to 150 at {(away - 100) / 100:,.2f}")  # fmt: skip
    x = c.delete(f"{acc}/orders/{cid}")
    check(x.status_code in (200, 202, 204) and until(lambda: get(cid)["status"] == "CANCELLED"),
          "cancelled")  # fmt: skip
    bad_tick = order(side="BUY", qty=75, order_type="LIMIT", price_paise=away + 3)
    bad_lot = order(side="BUY", qty=50, order_type="LIMIT", price_paise=away)
    reasons = [until(lambda r=r: get(r.json()["request"]["client_order_id"])["reason"])
               if r.status_code == 201 else r.json().get("detail") for r in (bad_tick, bad_lot)]  # fmt: skip
    check("tick" in str(reasons[0]), f"off the tick grid: refused, saying why ({reasons[0]})")
    check("lot" in str(reasons[1]), f"not a whole lot: refused, saying why ({reasons[1]})")

    print("--- positions and the broker check")
    rec = until(lambda: c.get(f"{acc}/positions").json()["reconcile"].get("checked_at"), 200)
    pos = c.get(f"{acc}/positions").json()
    check(rec and pos["reconcile"]["ok"] and not pos["reconcile"]["alerts"], f"the broker check ran ({rec}) and agrees: {pos['reconcile']}")

    print("--- protection")
    stop_at = int(last * 0.97) // 10 * 10
    sl = order(side="SELL", qty=75, order_type="SL-M", trigger_paise=stop_at,
               market_protection="2")  # fmt: skip
    sid = sl.json()["request"]["client_order_id"] if sl.status_code == 201 else None
    check(sid and until(lambda: get(sid)["status"] in ("OPEN", "TRIGGER_PENDING")),
          f"a sell SL-M rests at trigger {stop_at / 100:,.2f}: {sid and get(sid)['status']}")
    check(sid and c.delete(f"{acc}/orders/{sid}").status_code in (200, 202, 204)
          and until(lambda: get(sid)["status"] == "CANCELLED"), "and is cancelled")  # fmt: skip
    g = c.post(f"{acc}/gtt", json={"instrument_id": FUT, "product": "NRML", "kind": "single",
               "legs": [{"trigger_paise": stop_at, "side": "SELL", "qty": 75,
                         "price_paise": stop_at - 500}]})  # fmt: skip
    gid = g.json().get("gtt_id") if g.status_code == 201 else None
    listed = [v for v in c.get(f"{acc}/gtt").json() if v["gtt_id"] == gid]
    check(gid and listed and listed[0]["status"] == "active",
          f"a GTT stop is placed and listed active ({g.status_code}: {gid})")
    gone = c.delete(f"{acc}/gtt/{gid}") if gid else None
    check(gone is not None and gone.status_code == 200 and gone.json()["status"] == "deleted",
          "and deleted")  # fmt: skip

    print("--- square-off (intraday positions)")
    mis = order(side="BUY", qty=75, order_type="MARKET", market_protection="2", product="MIS")
    check(mis.status_code == 201 and until(lambda: net("MIS") == 75), "an MIS buy fills: +75")
    asked = c.post(f"{acc}/square-off", json={"confirm": False})
    check(asked.status_code == 422, "square-off without confirming is refused")
    done = c.post(f"{acc}/square-off", json={"confirm": True})
    check(done.status_code == 200 and until(lambda: net("MIS") == 0),
          f"square-off closes it: {done.json()}")  # fmt: skip
    check(net("NRML") == 75, "and leaves the overnight (NRML) position alone")

    print("--- webhooks")
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://localhost/x"):
        r = c.put("/api/alerts/webhook", json={"url": url})
        check(r.status_code == 422, f"refused: {url} ({r.json().get('detail')})")

    order(side="SELL", qty=75, order_type="MARKET", market_protection="2")
    until(lambda: net() == 0)
    subprocess.run(["pkill", "-f", "[-]m mocktrade[.]tools[.]demo"], check=False)
    print(f"\nPAPER: {sum(ok for ok, _ in results)}/{len(results)} steps passed. "
          "None of this is evidence about Kite.")  # fmt: skip
    sys.exit(0 if all(ok for ok, _ in results) else 1)


if __name__ == "__main__":
    main()
