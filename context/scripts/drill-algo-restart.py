"""#426 drill on the demo: an Autospreader working when the server dies comes back PAUSED.

    1. sign in, define a calendar, sell 1 spread lot above the market (the quote rests);
    2. kill the demo (SIGKILL: a crash, nothing flushed by a clean stop), start it again;
    3. sign in again: the algo is back with the same id, PAUSED, saying so; its quote is
       closed (paper orders don't survive a restart) and nothing was sent by itself;
    4. resume: it quotes again; stop: it ends.

The demo must be running (python -m laddrix.tools.demo --port 8000 --ui ui/dist); this
script kills and restarts it. Each sign-in waits for a new 30 s authenticator window.

    python3 context/scripts/drill-algo-restart.py
"""
import asyncio
import os
import signal
import subprocess
import time

import httpx

BASE = "http://localhost:8000"
ACC = "acc-paper"
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def demo_pids() -> list[int]:
    target = b"laddrix.tools." + b"demo"
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if b"-m" in args and target in args and b"--port" in args:
            found.append(int(pid))
    return found


async def sign_in(last: list[int]) -> httpx.AsyncClient:
    window = int(time.time() // 30)
    while last and window == last[0]:  # a code works once: wait for the next one
        await asyncio.sleep(1)
        window = int(time.time() // 30)
    last[:] = [window]
    code = subprocess.check_output(["python3", "-m", "laddrix.tools.demo", "--code"]).decode().strip()
    c = httpx.AsyncClient(base_url=BASE, headers={"origin": BASE}, timeout=30)
    r = await c.post("/api/session", json={"username": "demo", "password": "demo-password-123", "code": code})
    r.raise_for_status()
    return c


async def until(what, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (got := await what()):
            return got
        await asyncio.sleep(0.25)
    return None


async def main() -> None:
    last: list[int] = []
    c = await sign_in(last)
    legs = [{"instrument_id": "NFO:NIFTY26OCTFUT", "ratio": 1, "multiplier": 1, "quote": True},
            {"instrument_id": "NFO:NIFTY26NOVFUT", "ratio": -1, "multiplier": -1}]
    spread = (await c.post("/api/spreads", json={"name": f"drill {int(time.time()) % 10000}", "legs": legs})).json()
    algo = (await c.post(f"/api/accounts/{ACC}/spreads/{spread['spread_id']}/orders",
                         json={"side": "SELL", "lots": 1, "price_paise": -13000})).json()
    algo_id = algo["algo_id"]

    async def quoting():
        orders = (await c.get(f"/api/accounts/{ACC}/orders")).json()
        return [o for o in orders if o["request"]["parent_id"] == algo_id and o["status"] in ("OPEN", "PENDING")]

    first = await until(quoting)
    check("before: the ASE quotes", bool(first), f"{algo_id} {first and first[0]['request']['qty']}")
    await c.aclose()

    for pid in demo_pids():
        os.kill(pid, signal.SIGKILL)  # a crash
    await asyncio.sleep(1)
    subprocess.Popen(["python3", "-m", "laddrix.tools.demo", "--port", "8000", "--ui", "ui/dist"],
                     stdout=open("/tmp/claude-0/demo.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)

    async def up():
        try:
            async with httpx.AsyncClient(base_url=BASE, timeout=2) as h:
                return (await h.get("/api/health")).status_code == 200
        except httpx.HTTPError:
            return False

    check("the demo starts again", bool(await until(up, 40)))
    c = await sign_in(last)

    async def restored():
        found = [a for a in (await c.get(f"/api/accounts/{ACC}/algos")).json() if a["algo_id"] == algo_id]
        return found[0] if found and found[0]["state"] == "PAUSED" else None

    back = await until(restored)
    check("after: the same algo is back, PAUSED, saying so", bool(back),
          back and f"{back['state']}: {back['status']}")
    orders = (await c.get(f"/api/accounts/{ACC}/orders")).json()
    mine = [o for o in orders if o["request"]["parent_id"] == algo_id]
    check("its old quote is closed (paper orders don't survive) and nothing new was sent",
          len(mine) == 1 and mine[0]["status"] == "CANCELLED", "; ".join(f"{o['status']} {o['reason']}" for o in mine))
    await c.post(f"/api/accounts/{ACC}/algos/{algo_id}/resume", json={})
    again = await until(quoting)
    check("resume: it quotes again", bool(again), again and f"{again[0]['request']['side']} {again[0]['request']['qty']}")
    await c.post(f"/api/accounts/{ACC}/algos/{algo_id}/stop", json={})

    async def stopped():
        a = [x for x in (await c.get(f"/api/accounts/{ACC}/algos")).json() if x["algo_id"] == algo_id]
        return a and a[0]["state"] == "STOPPED"

    check("stop: it ends", bool(await until(stopped)))
    await c.aclose()
    print(f"\n{sum(results)}/{len(results)} passed")
    raise SystemExit(0 if all(results) else 1)


asyncio.run(main())
