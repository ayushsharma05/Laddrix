"""#223: check a real recording, e.g. the demo's after it has run for a while.

    python3 context/scripts/check-recording.py [data/demo/ticks]

Prints per day: ticks, bytes, bytes per tick, instruments, the busiest instruments, and how
fast the day reads back (every tick decoded). Measured 16 Oct 2026 on the demo's simulated
market (72 instruments, full depth): 86.6 bytes a tick; 6,408 ticks read back in 0.15 s."""
import sys
import time
from pathlib import Path

from mocktrade.research.ticks import FileTickStore

store = FileTickStore(Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo/ticks"))
for day in store.days():
    s = store.summary(day)
    n, b = s["ticks"], s["bytes"]
    print(day, f"ticks {n}", f"bytes {b}", f"bytes/tick {b / max(n, 1):.1f}",
          f"instruments {len(s['instruments'])}")
    print("  busiest:", sorted(s["instruments"].items(), key=lambda kv: -kv[1])[:3])
    t = time.time()
    m = sum(1 for _ in store.read(day))
    print(f"  read back {m} ticks in {time.time() - t:.2f} s", "OK" if m == n else "MISMATCH")
