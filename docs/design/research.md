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

## K11.2 Replay a recorded day into paper trading (#224)

```
python -m mocktrade.tools.demo --replay 2026-10-16 [--ticks data/demo/ticks] [--speed 10] --port 8001
python -m mocktrade.tools.demo --replay 2026-10-16 --code        # its login code

FileTickStore.read_timed(day) ─► ReplayFeed ─► ReplayClock.now = the tick's recorded time
                                            ─► MarketData.on_tick ─► paper broker, algos, screens
                                            ─► waits gap / speed (at most 5 s), or 3 loop turns at max
```

- **Its own server and data folder** (`data/replay/<day>/`): a user, paper account, audit trail,
  reports and MTM rules of its own. Practice never mixes with live or live-paper results, and the
  replay doesn't record what it replays.
- **Everything runs on the replayed time:** the paper broker, orders, risk, algos (TWAP/VWAP
  schedules), MTM, the square-off window, the scheduler (16:00 report), alerts and reports all
  get the `ReplayClock`.
- **The same fills as live, at any speed:**
  - In a replay the paper broker sees every tick (`PaperBroker(every_tick=True)` uses
    `MarketData.tap`). A subscription keeps only the newest tick per instrument, so at full
    speed it would skip ticks and the fills would depend on the speed.
  - At full speed the feed gives the event loop 3 turns between ticks. An order placed on one
    tick is accepted before the next, as it always is live, where ticks are milliseconds apart.
  - Tested: a scripted day gives identical fills live and replayed, every time.
- **Controls** (`GET/POST /api/replay`; every action audited as REPLAY_CONTROL):
  - pause, which stops before the next tick, not after it;
  - resume;
  - speed: 1×, 10×, 60× or max;
  - jump ahead: the ticks in between are fed at once, so prices, VAP and resting orders stay
    consistent. To go back, start the replay again. A jump past the end finishes the day.
- **Screens:**
  - a purple **replay bar** (day, replayed time, state, speed buttons, Pause/Resume, +15 min);
  - a **REPLAY** band ("practice on a recorded day; nothing reaches the market");
  - every widget unchanged.
- **Limits:**
  - The replay knows the demo's instruments. A Kite recording replays once the instrument list is
    loaded the same way (a later step).
  - A recording's own pauses (between sessions, or a dropped feed) are shortened to 5 s.

Checks:
- `tests/test_replay.py`: pace and clock, pause/resume/speed/jump, the same fills as live, the API, the demo flag;
- `ui/src/shell/ReplayBar.test.tsx`;
- `context/scripts/mutate-replay.py` (11/11);
- real browser `context/scripts/e2e-replay.cjs <day>`, against a replay of a demo recording.
