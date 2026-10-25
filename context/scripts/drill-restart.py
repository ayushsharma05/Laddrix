"""Crash-and-restart drill on the demo (V1.2, #328). Run from the repo root with no demo running:

    python3 context/scripts/drill-restart.py            # prints each check, PASS/FAIL; exit 1 if any fail

1. start a fresh demo; buy 150 at the market (fills), rest a buy limit far away, and protect the
   position with a trailing stop (held by Laddrix)
2. SIGKILL the server mid-session (no clean shutdown), and start it again
3. check: the position is rebuilt to 150 (bought 150, not 300: no fill counted twice), the
   resting order's fate is said (the paper broker restarted empty, so it is closed, not left
   "working" forever), the lost stop shows an UNPROTECTED band, cancels and new orders work
4. protect it again: the band clears; kill and restart once more: still 150, still no double count
5. close the position: nothing left to warn about

The browser half (the bands on screen, and the stale-prices band when the server freezes) is
context/scripts/e2e-restart-bands.cjs, run right after this with the demo still up.
"""

import os
import signal
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:8000"
FUT = "NFO:NIFTY26OCTFUT"
ACC = "acc-paper"
AWAY = 0  # a buy limit price that rests (set from the first fill)
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def start() -> subprocess.Popen:
    log = open("data/drill-demo.log", "a")
    proc = subprocess.Popen([sys.executable, "-m", "laddrix.tools.demo"], stdout=log, stderr=log)
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the demo did not start")


def kill(proc: subprocess.Popen) -> None:
    os.kill(proc.pid, signal.SIGKILL)  # a crash: no shutdown code runs
    proc.wait()


def login() -> httpx.Client:
    window = int(time.time()) // 30
    while int(time.time()) // 30 == window:  # a fresh code (each one works once)
        time.sleep(0.5)
    code = subprocess.run([sys.executable, "-m", "laddrix.tools.demo", "--code"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    c = httpx.Client(base_url=BASE, headers={"origin": ORIGIN}, timeout=10)
    r = c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                     "code": code})  # fmt: skip
    r.raise_for_status()
    return c


def state(c: httpx.Client) -> dict:
    pos = c.get(f"/api/accounts/{ACC}/positions").json()
    mine = [p for p in pos["positions"] if p["instrument_id"] == FUT]
    net = sum(p["qty"] for p in mine)
    bought = sum(p["buy_qty"] for p in mine)  # every fill counted: a replayed one would add up
    ltp = max((p["ltp_paise"] or 0 for p in mine), default=0)
    orders = {o["request"]["client_order_id"]: o
              for o in c.get(f"/api/accounts/{ACC}/orders").json()}  # fmt: skip
    bands = [a["text"] for a in c.get("/api/ops").json()["alerts"]]
    return {"net": net, "bought": bought, "ltp": ltp, "orders": orders, "bands": bands,
            "recovery": pos.get("recovery")}  # fmt: skip


def unprotected(s: dict) -> list[str]:
    return [b for b in s["bands"] if b.startswith("UNPROTECTED")]


def wait_for(c, test, seconds=12):
    end = time.time() + seconds
    while True:
        s = state(c)
        if test(s) or time.time() > end:
            return s
        time.sleep(0.5)


def order(c, **fields):
    body = {"instrument_id": FUT, "side": "BUY", "product": "NRML", **fields}
    return c.post(f"/api/accounts/{ACC}/orders", json=body)


def main() -> None:
    subprocess.run(["pkill", "-f", "[-]m laddrix[.]tools[.]demo"], check=False)
    time.sleep(1)
    subprocess.run(["rm", "-rf", "data/demo", "data/drill-demo.log"], check=True)
    proc = start()
    c = login()

    print("--- 1. a normal session")
    bought = order(c, order_type="MARKET", qty=150, market_protection="2")
    check(bought.status_code == 201, "buy 150 at the market")
    filled = wait_for(c, lambda s: s["net"] == 150)
    check(filled["net"] == 150, f"bought 150 (position {filled['net']})")
    global AWAY
    AWAY = int(filled["ltp"] * 0.97) // 10 * 10  # 3% under the market: it rests
    resting = order(c, order_type="LIMIT", qty=75, price_paise=AWAY)
    rest_id = resting.json()["request"]["client_order_id"]
    rest = wait_for(c, lambda s: s["orders"][rest_id]["status"] == "OPEN", seconds=5)
    check(rest["orders"][rest_id]["status"] == "OPEN", f"a buy limit rests at {AWAY / 100:,.2f}")
    before = state(c)
    stop = c.post(f"/api/accounts/{ACC}/algos", json={
        "code": "TRAILING_STOP", "instrument_id": FUT, "side": "SELL", "qty": 150,
        "params": {"trail_paise": 20_000}})  # fmt: skip
    check(stop.status_code == 201, f"trailing stop started ({stop.json().get('algo_id')})")
    time.sleep(1.5)  # the event log writes in the background: let it catch up, like a real day
    check(not unprotected(state(c)), "no UNPROTECTED band while the stop runs")

    print("--- 2. SIGKILL mid-session, then start again")
    kill(proc)
    proc = start()
    c = login()
    after = wait_for(c, lambda s: unprotected(s) and (s["recovery"] or {}).get("state") != "recovering")
    check(after["net"] == 150, f"position rebuilt to 150 (got {after['net']})")
    check(after["bought"] == before["bought"] == 150,
          f"no fill counted twice: bought {before['bought']} before, {after['bought']} after")
    old = after["orders"].get(rest_id, {})
    check(old.get("status") in ("CANCELLED", "REJECTED", "LAPSED", "EXPIRED") and old.get("reason"),
          f"the resting order's fate is said: {old.get('status')} ({old.get('reason')})")
    bands = unprotected(after)
    check(len(bands) == 1 and "150 NFO:NIFTY26OCTFUT (long)" in bands[0]
          and "was running when the server stopped" in bands[0],
          f"UNPROTECTED band: {bands[0] if bands else 'none'}")  # fmt: skip
    new = order(c, order_type="LIMIT", qty=75, price_paise=AWAY)
    check(new.status_code == 201, "a new order is accepted after recovery")
    gone = c.delete(f"/api/accounts/{ACC}/orders/{new.json()['request']['client_order_id']}")
    check(gone.status_code in (200, 202, 204), f"and cancelled ({gone.status_code})")

    print("--- 3. protect it again; crash again")
    again = c.post(f"/api/accounts/{ACC}/algos", json={
        "code": "TRAILING_STOP", "instrument_id": FUT, "side": "SELL", "qty": 150,
        "params": {"trail_paise": 20_000}})  # fmt: skip
    check(again.status_code == 201, "a new trailing stop")
    cleared = wait_for(c, lambda s: not unprotected(s), seconds=8)
    check(not unprotected(cleared), "the band clears once it is protected again")
    time.sleep(1.5)
    kill(proc)
    proc = start()
    c = login()
    twice = wait_for(c, lambda s: unprotected(s))
    check(twice["net"] == 150 and twice["bought"] == 150,
          f"a second crash: still {twice['net']} open, bought {twice['bought']}")  # fmt: skip
    check(len(unprotected(twice)) == 1, "and the second lost stop is named, once")

    print("--- 4. close it: nothing left to warn about")
    close = order(c, side="SELL", order_type="MARKET", qty=150, market_protection="2")
    check(close.status_code == 201, "sell 150 at the market")
    flat = wait_for(c, lambda s: s["net"] == 0 and not unprotected(s))
    check(flat["net"] == 0 and not unprotected(flat), "flat, and the band is gone")
    print(f"\n{sum(ok for ok, _ in results)}/{len(results)} checks passed; demo left running "
          "(run e2e-restart-bands.cjs next, or stop it)")  # fmt: skip
    sys.exit(0 if all(ok for ok, _ in results) else 1)


if __name__ == "__main__":
    main()
