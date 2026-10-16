# Research: recorded ticks, replay, backtests (K11)

## K11.1 Tick recorder (#223)

```
feed ─► MarketData.on_tick ─► subscribers (screens, algos, positions)
                          └─► taps ─► TickRecorder.on_tick   (one append: the trading loop never waits)
every 1 s, in a worker thread ─► FileTickStore.append(batch)
    <data>/ticks/<YYYY-MM-DD>/<segment>.ticks.gz   one gzip member per flush, India day
    <data>/ticks/<YYYY-MM-DD>/index.json           ticks per instrument
16:30 IST daily ─► rotate(): keep the newest MOCKTRADE_RECORD_DAYS days (default 30; 0 = off)
```

- **What is recorded:** every tick MockTrade receives, exactly as `MarketData` saw it, with every
  field (depth, volume, OI, both times) and the moment it was recorded. The feed only streams
  watched instruments (screens, algos, positions), so that is what gets recorded. To always
  record more, the recorder takes a `record(instrument_id)` choice.
- **Fidelity:** a tick read back equals the tick written, field by field. Times are kept as exact
  epoch microseconds and read back in UTC (the same instants).
- **Crash safety:** each flush appends a whole gzip member and is fsynced, so a crash loses at most
  the last second. A member cut short by a crash is skipped on reading, and everything before it
  is kept. A failed write (disk full) keeps the ticks for the next flush and counts the failure.
- **Order:** `read(day, instruments)` merges the segment files in the order the ticks were recorded.
- **The store is replaceable** (`TickStore` port): files today, a column store or object storage later.
- **Size, measured** on the demo's simulated market (72 instruments, full 5-level depth):
  - **86.6 bytes a tick** compressed;
  - at about one tick a second for a 6¼-hour session, that is **≈ 2 MB per instrument-day**;
  - for 100 instruments, about 195 MB a day and about 6 GB for 30 days.

  Choose `MOCKTRADE_RECORD_DAYS` for your disk; the disk check warns before it fills.
- **Reading speed:** about 43,000 ticks a second, decoding every field. A 100-instrument day
  (≈ 2.25 M ticks) reads in under a minute: fine for replay (K11.2) and backtests (K11.3).

**API:**
- `GET /api/research/recordings` lists the days, newest first, with ticks, bytes, segments and instrument count.
- `GET /api/research/recordings/{day}` gives the ticks per instrument.

**Checks:**
- `tests/test_ticks_recorder.py`;
- `context/scripts/mutate-recorder.py` (14/14);
- a real recording of the demo: `context/scripts/check-recording.py`.
