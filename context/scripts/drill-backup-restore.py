"""Backup-and-restore drill on the demo (V1.3, #329). Run from the repo root, no demo running:

    python3 context/scripts/drill-backup-restore.py    # PASS/FAIL per check; exit 1 if any fail

1. a fresh demo gets a day's worth of state: a second user, a trade and a position, a saved
   layout, a theme, an MTM limit, a market alert
2. while it runs, `laddrix.tools.backup` backs it up (the server keeps writing meanwhile)
3. the server stops; the restore goes into an ISOLATED, EMPTY folder first, never over the
   live one; the demo's secret key and login secret are put back "from the password manager"
   (copied by hand: they are never in a backup)
4. a server starts on the restored folder (the live one moved aside, kept): everything from
   step 1 is there, and the audit trail has every event; then the original folder comes back
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE, ORIGIN = "http://127.0.0.1:8000", "http://localhost:8000"
DEMO = Path("data/demo")
WORK = Path("data/drill-backup")
ACC, FUT = "acc-paper", "NFO:NIFTY26OCTFUT"
results: list[tuple[bool, str]] = []


def check(ok, what):
    results.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def stop():
    subprocess.run(["pkill", "-f", "[-]m laddrix[.]tools[.]demo"], check=False)
    time.sleep(1.5)


def start():
    log = open(WORK / "demo.log", "a")
    subprocess.Popen([sys.executable, "-m", "laddrix.tools.demo"], stdout=log, stderr=log)
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the demo did not start")


def login():
    window = int(time.time()) // 30
    while int(time.time()) // 30 == window:  # a fresh code: each works once
        time.sleep(0.5)
    code = subprocess.run([sys.executable, "-m", "laddrix.tools.demo", "--code"],
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    c = httpx.Client(base_url=BASE, headers={"origin": ORIGIN}, timeout=10)
    c.post("/api/session", json={"username": "demo", "password": "demo-password-123",
                                 "code": code}).raise_for_status()  # fmt: skip
    return c


def snapshot(c):
    pos = c.get(f"/api/accounts/{ACC}/positions").json()["positions"]
    events, before = [], None
    while True:  # the whole audit trail, page by page
        page = c.get(f"/api/accounts/{ACC}/events",
                     params={"limit": 200, **({"before_seq": before} if before else {})}).json()
        events += page
        if len(page) < 200:
            break
        before = page[-1]["seq"]
    return {
        "users": sorted(u["id"] for u in c.get("/api/admin/users").json()),
        "position": sum(p["qty"] for p in pos if p["instrument_id"] == FUT),
        "bought": sum(p["buy_qty"] for p in pos if p["instrument_id"] == FUT),
        "layout": c.get("/api/layouts/drill").json().get("layout"),
        "theme": c.get("/api/me/settings/theme").json().get("value"),
        "mtm": c.get(f"/api/accounts/{ACC}/mtm").json().get("portfolio"),
        "alerts": sorted((a["kind"], a["instrument_id"]) for a in c.get("/api/alerts").json()),
        "audit": sorted((e["seq"], e.get("kind") or e.get("exec_type") or "") for e in events),
    }


def main():
    stop()
    shutil.rmtree(DEMO, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    start()
    c = login()

    print("--- 1. a day's worth of state")
    made = c.post("/api/admin/users", json={"id": "ravi", "name": "Ravi", "roles": ["trader"]})
    check(made.status_code == 201, f"a second user ({made.status_code})")
    c.post(f"/api/accounts/{ACC}/orders", json={"instrument_id": FUT, "side": "BUY", "qty": 75,
           "order_type": "MARKET", "product": "NRML", "market_protection": "2"})  # fmt: skip
    time.sleep(2)
    check(c.put("/api/layouts/drill", json={"layout": {"widgets": ["ladder", "ticket"],
                                                       "drill": True}}).status_code == 200,
          "a saved layout")  # fmt: skip
    check(c.put("/api/me/settings/theme", json={"value": {"name": "cb"}}).status_code == 200,
          "the colour-blind theme")
    check(c.put(f"/api/accounts/{ACC}/mtm/portfolio", json={"max_loss_paise": 500_000}).status_code
          == 200, "an MTM loss limit")  # fmt: skip
    alert = c.post("/api/alerts", json={"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                        "params": {"level_paise": 2_600_000}})  # fmt: skip
    check(alert.status_code == 201, f"a market alert ({alert.status_code})")
    time.sleep(1.5)  # the audit trail is written in the background
    before = snapshot(c)
    print("   state:", json.dumps({k: v for k, v in before.items() if k != "audit"}),
          f"audit events: {len(before['audit'])}")

    print("--- 2. back up while the server runs")
    backup = subprocess.run([sys.executable, "-m", "laddrix.tools.backup", "--data", str(DEMO),
                             "--to", str(WORK / "backups")], capture_output=True, text=True)  # fmt: skip
    check(backup.returncode == 0, backup.stdout.strip() or backup.stderr.strip())
    (folder,) = (WORK / "backups").iterdir()
    names = sorted(json.loads((folder / "manifest.json").read_text())["files"])
    check(not any(n.endswith((".key", "totp.txt")) or "kite_token" in n for n in names),
          f"no secret in it: {names}")  # fmt: skip
    c.post(f"/api/accounts/{ACC}/orders", json={"instrument_id": FUT, "side": "BUY", "qty": 75,
           "order_type": "MARKET", "product": "NRML", "market_protection": "2"})  # fmt: skip
    time.sleep(1.5)  # a trade after the backup: the restore must NOT have it
    stop()

    print("--- 3. restore into an isolated, empty folder")
    fresh = WORK / "restored"
    r = subprocess.run([sys.executable, "-m", "laddrix.tools.restore", "--from", str(folder),
                        "--to", str(fresh)], capture_output=True, text=True)  # fmt: skip
    check(r.returncode == 0 and "LADDRIX_SECRET_KEY" in r.stdout, r.stdout.splitlines()[0])
    refused = subprocess.run([sys.executable, "-m", "laddrix.tools.restore", "--from",
                              str(folder), "--to", str(DEMO)], capture_output=True, text=True)  # fmt: skip
    check(refused.returncode == 1 and "is not empty" in refused.stderr,
          "over the live folder without --replace: refused")
    for secret in ("secret.key", "demo_totp.txt"):  # "from the password manager"
        shutil.copy2(DEMO / secret, fresh / secret)

    print("--- 4. a server on the restored folder")
    live = WORK / "live-aside"
    DEMO.rename(live)
    fresh.rename(DEMO)
    try:
        start()
        c = login()
        time.sleep(2)  # recovery
        after = snapshot(c)
        print("   state:", json.dumps({k: v for k, v in after.items() if k != "audit"}),
              f"audit events: {len(after['audit'])}")
        for key in before:
            if key != "audit":
                check(after[key] == before[key], f"{key}: {before[key]!r} -> {after[key]!r}")
        kept = [e for e in after["audit"] if e in before["audit"]]
        added = [kind for e, kind in after["audit"] if (e, kind) not in before["audit"]]
        check(kept == before["audit"], f"every audit event kept ({len(kept)} of "
              f"{len(before['audit'])})")  # fmt: skip
        check(all("RECOVER" in k for k in added), f"and only the restart's own added: {added}")
        check(after["position"] == 75, "the trade made after the backup is not in it: "
              "a restore is the backup's moment")  # fmt: skip
    finally:
        stop()
        shutil.rmtree(DEMO, ignore_errors=True)
        live.rename(DEMO)  # the original comes back
    print(f"\n{sum(ok for ok, _ in results)}/{len(results)} checks passed")
    sys.exit(0 if all(ok for ok, _ in results) else 1)


if __name__ == "__main__":
    main()
