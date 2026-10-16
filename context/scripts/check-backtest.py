"""#225: run a real backtest over a recording (the demo's) and print what it found.

    python3 context/scripts/check-backtest.py [data/demo/ticks] [ALGO] [INSTRUMENT]

Uses the demo's instruments; TWAP buy of 2 lots over 1 minute by default, from the first tick."""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

from mocktrade.charges.model import ScheduleCharges
from mocktrade.charges.schedules import KITE_STANDARD
from mocktrade.marketdata.sim_universe import INSTRUMENTS
from mocktrade.refdata.store import InstrumentStore
from mocktrade.research.backtest import Backtests, BacktestSpec
from mocktrade.research.ticks import IST, FileTickStore


async def main() -> None:
    ticks = FileTickStore(Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo/ticks"))
    algo = sys.argv[2] if len(sys.argv) > 2 else "TWAP"
    instrument = sys.argv[3] if len(sys.argv) > 3 else "NFO:NIFTY26OCTFUT"
    store = InstrumentStore()
    for i, _ in INSTRUMENTS:
        store.add(i)
    days = ticks.days()
    first_us, _ = next(ticks.read_timed(days[0]))
    from mocktrade.research.replay import EPOCH
    from datetime import timedelta

    start = (EPOCH + timedelta(microseconds=first_us)).astimezone(IST).strftime("%H:%M")
    params = {"duration_min": 1, "interval_s": 10} if algo == "TWAP" else {}
    runs = Backtests(Path(tempfile.mkdtemp()), store, ScheduleCharges(KITE_STANDARD),
                     ticks=lambda d: ticks.read_timed(d) if d in days else None)
    spec = BacktestSpec(tuple(d.isoformat() for d in days), algo, instrument, "BUY", 150, params,
                        start=start)
    t = time.time()
    run = runs.start(spec)
    await runs.settle()
    print(f"{run.state} in {time.time() - t:.1f} s {run.error}")
    if run.result:
        s = run.result["summary"]
        print({k: s[k] for k in ("days_run", "trades", "gross_paise", "charges_paise", "net_paise",
                                 "max_drawdown_paise", "win_rate")})
        for d in run.result["daily"]:
            if "trades" in d:
                print(d["day"], "ticks fed; trades:",
                      [(x["ts"][11:19], x["qty"], x["price_paise"]) for x in d["trades"]])


asyncio.run(main())
