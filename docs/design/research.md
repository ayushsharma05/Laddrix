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

## K11.3 Backtest runner (#225)

```
POST /api/research/backtests {days, algo, instrument_id, side, qty, params, product, start, capital_paise, candles?}
  ─► Backtests (one run at a time, in the background) ─► per day, a fresh platform on a ReplayClock:
       recorded ticks (or ticks made from candles), as fast as possible; each tick:
         paper broker (every tick) ─► positions marked ─► algo engine on_tick
         every 100 ms of replayed time: the engine's heartbeat (its timers)
         between ticks: drain (the broker idle, the engine not busy), so each fill carries
                        the time of the tick that caused it
       at `start` (India time): the algo starts, with the normal risk checks
       the day's end: the K10.3 report (gross, charges, net) at the last prices + intraday equity
  ─► the run: equity curve (cumulative net by day), max drawdown, winning days (net > 0), trades
  ─► kept: <data>/backtests/<run>/run.json (+ each day's audit trail)
GET /api/research/backtests, GET /api/research/backtests/{run}
POST /api/research/candles {instrument_id, day, rows: [{time, open, high, low, close, volume?}]}
```

- **Importing candles safely** (#269, after an outside review reproduced a path escaping its folder):
  - **The contract must be known.** It must be in the instrument list; otherwise the server answers 422.
  - **The store checks the id itself.** An id must be `EXCHANGE:SYMBOL` with no path characters, and its folder must sit directly under the candle folder. A planted link is not followed outside.
  - **Each row is checked:**
    - time with its zone, on that day, and no second candle at the same time;
    - whole paise for prices, and low ≤ open, close ≤ high;
    - a whole volume that isn't negative;
    - no unknown fields.
  - **Written whole or not at all.** The day goes to a `.part` file and is then moved into place, so a refused import leaves the earlier day as it was.
  - **Recorded.** Each import is audited (`CANDLES_IMPORTED`: who, contract, day, rows).
  - The folder layout is unchanged, so data imported before still reads.
  - Account ids also become file names (MTM rules, broker tokens, reports), so the store accepts only letters, digits and dashes for them, from the API and the terminal tool alike.
- **The same code as live:** the real algo engine, paper broker, risk gate, charges and report.
  A backtest can't be kinder than the platform would have been that day.
- **Deterministic:** nothing sleeps or reads the wall clock. The same days and spec give the same
  report, byte for byte (tested).
- **Hand-worked** (`tests/test_backtest.py`): SLICER buys 2 × 75 crossing the spread on a tiny day.
  Fills at 25,000.10 and 25,002.10 at the times of their ticks; ₹585.00 unrealised at the last
  price; charges equal to the K10.2 bill for those two orders.
- **Candles** (when no ticks were recorded) become ticks: open, then the adverse extreme first
  (the low of a rising candle, the high of a falling one), the other extreme, then the close.
  Each has one tick of spread, so market orders pay it and resting orders fill only when the price
  trades through them. The run is labelled `candle_based`. Imported rows are checked
  (low ≤ open/close ≤ high, time zone, day).
- **Runs survive a restart.** A run that was going when the server stopped is marked failed,
  saying so, never left "running".
- **Speed:** the demo's recording (5,328 ticks, 72 instruments) runs in 0.3 s
  (`context/scripts/check-backtest.py`).
- **Limits:** one algo per run; strategies that trade on their own signals come later. Positions
  are valued at the day's last price (MTM), not closed.

Checks:
- `tests/test_backtest.py`;
- `context/scripts/mutate-backtest.py` (12/12);
- `context/scripts/check-backtest.py` on the demo recording.

## K11.4 Research screens (#226)

Two widgets (Widgets ▾): **Recordings** and **Backtests**. Every figure, choice and default comes
from the server; the screens hold no market rules and know no algo.

```
 Recordings                         Backtests
 GET /api/research/recordings       GET  /api/research/backtests/form[?instrument_id=]
     day · ticks · size · segments       algos (with their settings), recorded days, products,
 GET /api/research/recordings/{day}      defaults (start, capital, product), max days,
     a day's instruments, busiest first  candle days of the contract
                                    POST /api/research/backtests          start a run
                                    GET  /api/research/backtests          runs (polled each 1 s
                                                                          while one is going)
                                    GET  /api/research/backtests/{run}    summary + days
                                    GET  …/{run}/days/{day}?format=json|csv|pdf
                                         the K10.3 report of that day (same CSV/PDF writers)
```

- **New:** the algo form reuses the algo screen's parts (`ParamInput`, `buildAlgoBody`): pick
  the algo, contract, side, lots, product, settings, start time (IST), capital, name and days
  (All / None, or from imported candles). Days are sent in order. The server checks everything
  again, and now refuses an unknown algo up front (before, the run started and failed).
- **Runs:** each run's state in words ("Running: 2 of 5 days", "Failed: …", "Done (1 day
  skipped)"). The list is asked again each second only while a run is queued or running, then
  stops. The picked run is read again whenever its state or progress changes.
- **A run:** totals (net, gross, charges, max drawdown, winning days and rate, trades), the
  cumulative net by day as a line, and the days table. A skipped day says why and has no
  downloads. A day opens to its own P&L curve through the day (before charges, sampled each
  minute), its trades and rejections, with CSV and PDF.
- **Compare:** two to four finished runs. Each picked run takes a colour slot and keeps it while
  others come and go (colour follows the run, never its position). The chart covers every day
  any of them ran, with a gap where a run didn't. Below it is a table of the figures side by side.
- **The chart** (`LineChart.tsx`, plain SVG, no library):
  - 2 px lines; zero is always in range and drawn dashed;
  - the highest and lowest values on the left, the first and last x below;
  - hover shows a crosshair and each series' value; a legend appears from two series;
  - the SVG's accessible name says where each series ends.
  - The four slot colours were run through a colour-blind validator against the dark surface
    (all checks pass; worst adjacent pair ΔE 8.4).

Checks:
- `tests/test_backtest.py` (form, day JSON/CSV/PDF, skipped/unknown/unfinished days);
- `ui/src/widgets/research/*.test.ts(x)`;
- `context/scripts/mutate-research.py`;
- `context/scripts/e2e-research.cjs` (real browser on the demo's recording).

## K11.5 Research follow-ups (#235)

Two parts of #226 that K11.4 left out.

- **Delete a recorded day:** `DELETE /api/research/recordings/{day}`.
  - Admin only (403 otherwise).
  - Refused for today's India day, which is still being written (409).
  - A missing day gives 404.
  - Audited as `RECORDING_DELETED` with who and how many bytes.
  - The listing marks each day `deletable` for this user, so the screen never guesses what "today" is.
  - The Recordings widget asks for a second click ("Confirm: delete <day> (<size>)") before sending.
- **Replay from the screen:** a replay runs as its own server with its own data folder, so practice can never mix with live. The live server is not switched into replay. The server publishes the command for each day (`replay`) only where its days can be replayed, which today means the demo. The screen shows the command with a Copy button.
  - A server that starts and stops replay processes itself (another port, its own login) belongs with the K13 admin panel.
  - **Known gap:** replaying a *live* (Kite) server's recordings needs that day's instrument list; the replay tool uses the demo's instruments. This is still to build before practising on real recorded days.

Checks:
- `tests/test_ticks_recorder.py`;
- `ui/src/widgets/research/research.test.tsx`;
- `context/scripts/mutate-recordings.py` (13/13);
- `context/scripts/e2e-recordings.cjs`, a real browser on the demo. It deleted an older day (files gone, audit row written), today offered no delete, and the copied replay command was run and started a replay server.

## V1.1 Each recorded day keeps its instrument list (#327)

**Why:** a recording used to keep only ticks. Instrument details (lot, tick, expiry) came from the live instrument list, which is replaced every morning. So a day with a contract that has since expired, or a day recorded on another server, could replay its prices, but every order on it failed with "unknown instrument".

**What happens now:**

    tick arrives ─► TickRecorder (on the trading loop: one append)
                 ─► every second, in a worker thread: FileTickStore.append(batch)
                       1. instruments new to this day's list ─► instrument master ─► instruments.json
                       2. then the ticks ─► <segment>.ticks.gz

- **What is saved:** `<day>/instruments.json` holds, for each instrument recorded that day: exchange, symbol, kind, tick, lot, expiry, strike and underlying (`instrument_record()` in `refdata/store.py`). Each instrument is described once a day, before its first ticks are written, so a crash can't leave ticks nobody can describe.
- **After a restart:** the list on disk is added to, not replaced. An instrument the master doesn't know yet is left out and asked about again later.
- **Replaying** (`python -m mocktrade.tools.demo --replay <day> --ticks <folder>`):
  - The server is built from the day's own list (`replay_instruments()` in `research/replay.py`). It needs no broker login and no live list.
  - Days recorded before this change have no list. They still replay if every instrument is one of the demo's own.
  - Otherwise the replay refuses to start, naming the day and the instruments with no details (the first five, then "and N more"), and creates nothing. Refusing is better than trading prices you can't place orders on.
  - A broken list is refused the same way, naming the file.
- **Evidence:** `context/scripts/e2e-recording-instruments.sh` on the demo:
  1. The demo recorded 72 instruments and described all 72.
  2. An expired, non-demo contract (BANKNIFTY25JANFUT on 20 Jan 2025) was replayed with the real command. The contract was found with that day's lot size (30). A two-lot buy was accepted. A 45-quantity buy was refused: "45 is not a multiple of BANKNIFTY25JANFUT's lot size 30".
  3. With its list removed, the same day refused to start and said why.
- **Checks:** `tests/test_recording_instruments.py` (17 tests); `context/scripts/mutate-recording-instruments.py` (22/22 caught; it found four gaps in the tests, now covered).
- **Known limit:** a **backtest** still looks contracts up in the live instrument list, so a backtest of an expired contract's day is refused with "unknown instrument". The day's list makes this fixable; it is noted for the hardening phase.
