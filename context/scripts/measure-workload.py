"""#274: how late does the trading loop run while a backtest or a report is being made?

A probe on the loop asks to wake every 5 ms and records how late each wake is: the delay
every price, risk check, order and screen update would see at that moment. Four runs:

    idle                       nothing else running
    backtest on the loop       how it was before #274 (InProcess)
    backtest in the worker     how it is now (Worker: its own process)
    report on the loop / in a thread

Prints p50, p95, p99 and max in ms. Run alone (it uses all of one CPU for a while):
    PYTHONPATH=src python3 context/scripts/measure-workload.py [ticks]
"""

import asyncio
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from laddrix.charges.model import ScheduleCharges
from laddrix.charges.schedules import KITE_STANDARD
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.refdata.store import InstrumentStore
from laddrix.research.backtest import BacktestSpec, InProcess, RecordedDay, Worker
from laddrix.research.ticks import FileTickStore

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 16)
FUT = Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, tick_paise=10, lot_size=75,
                 expiry=date(2026, 10, 27), underlying="NIFTY")  # fmt: skip


def record_day(root: Path, n: int) -> None:
    store = FileTickStore(root)
    start = datetime(2026, 10, 16, 9, 15, tzinfo=IST)
    batch = []
    for i in range(n):
        at = start + timedelta(milliseconds=200 * i)
        ltp = 2500000 + 10 * ((i * 7) % 41 - 20)
        tick = Tick(FUT.id, ltp, received_at=at, volume=75 * i,
                    bids=(DepthLevel(ltp - 10, 750, 3),), asks=(DepthLevel(ltp + 10, 750, 3),))  # fmt: skip
        batch.append((tick, at))
        if len(batch) == 5000:
            store.append(batch)
            batch = []
    if batch:
        store.append(batch)


async def probe(stop: asyncio.Event, lags: list[float]) -> None:
    while not stop.is_set():
        asked = time.perf_counter()
        await asyncio.sleep(0.005)
        lags.append((time.perf_counter() - asked - 0.005) * 1000)


async def measure(name: str, work) -> None:
    stop, lags = asyncio.Event(), []
    p = asyncio.create_task(probe(stop, lags))
    started = time.perf_counter()
    await work()
    took = time.perf_counter() - started
    stop.set()
    await p
    q = statistics.quantiles(lags, n=100)
    print(f"{name:28} p50 {q[49]:6.1f}  p95 {q[94]:6.1f}  p99 {q[98]:6.1f}  "
          f"max {max(lags):7.1f} ms   ({took:.1f} s, {len(lags)} wakes)")  # fmt: skip


async def main(n: int) -> None:
    instruments = InstrumentStore()
    instruments.add(FUT)
    charges = ScheduleCharges(KITE_STANDARD)
    spec = BacktestSpec((DAY.isoformat(),), "SLICER", FUT.id, "BUY", 750,
                        {"slices": 10, "interval_s": 60, "mode": "cross"})  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record_day(root / "ticks", n)
        day = RecordedDay(root / "ticks", DAY)
        print(f"{n} recorded ticks, Python {sys.version.split()[0]}")
        await measure("idle", lambda: asyncio.sleep(2))
        result = {}

        async def on_loop():
            result["loop"] = await InProcess().day(spec, DAY, day, instruments, charges,
                                                   root / "a")  # fmt: skip

        worker = Worker()

        async def in_worker():
            result["worker"] = await worker.day(spec, DAY, day, instruments, charges, root / "b")

        await measure("backtest on the loop (before)", on_loop)
        await measure("backtest in the worker (now)", in_worker)
        worker.close()
        same = result["loop"]["totals"] == result["worker"]["totals"]
        print(f"same result both ways: {same}")

        from laddrix.reports.daily import build_day
        from laddrix.store.events import EventLog

        log = EventLog(root / "a" / f"{DAY.isoformat()}.db")

        def report():
            return build_day("backtest", DAY, log, instruments.get, charges, prices=None,
                             now=datetime.now(IST), final=False)  # fmt: skip

        async def report_on_loop():
            for _ in range(20):
                report()
                await asyncio.sleep(0)

        async def report_in_thread():
            for _ in range(20):
                await asyncio.to_thread(report)

        await measure("20 reports on the loop", report_on_loop)
        await measure("20 reports in a thread (now)", report_in_thread)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 40_000))
