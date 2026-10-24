# Algos: parent orders worked by strategies

```
trader ─► start(code, instrument, side, qty, params) ─► parent A1 ─► strategy
strategy ─► ctx.send / ctx.modify / ctx.cancel ─► order manager (same risk, throttle, broker as clicks)
order events with parent_id = A1 ─► parent's filled and average from its children ─► strategy.on_child
prices ─► strategy.on_tick            heartbeat every 100 ms ─► strategy.on_timer
pause ─► pull working children        stop / kill switch ─► pull every child ─► STOPPED
```

## Replaceable parts
- **Strategy** (`algos/engine.py: Strategy`): a small interface (start, on_tick, on_child,
  on_timer, on_pause, on_resume) acting only through `AlgoContext`. A new algo is a new class;
  the engine, the order path and the screens don't change.
- **Algo spec** (`algos/spec.py`): every algo publishes its parameters (name, label, kind,
  limits, default, help). The algo screen draws its form from it and the engine checks values
  against the same description (backend provides, frontend displays). Unknown settings are
  refused.
- **AlgoType**: spec + factory + the exchange's algo id once registered (SEBI framework; set
  with `MOCKTRADE_ALGO_IDS`). Its children carry it (`algo_id`); without one they are marked
  as algo orders by `parent_id`, and the algo can't start if the account may send more than
  10 orders a second ([compliance.md](compliance.md#the-exchange-algo-id), #291).

## Rules (each guards against a real failure)
| Trap | Rule |
|---|---|
| Algo orders skipping safety | children go through the same order manager, risk checks and throttle as clicks; the throttle lets them wait their turn |
| Over-sending | filled + working + sent-but-not-registered + a new child never exceeds the parent |
| Double-counted fills | parent filled = sum of its children's own filled quantities, recomputed |
| Unknown child outcome | the algo is held: nothing more is sent until the broker answers |
| A child sent just before pause/stop | pulled as soon as it registers; the algo isn't finished while one is on its way |
| Strategy bug | only that algo fails; its children are pulled |
| Broker keeps refusing | 3 refusals in a row fail the algo with the last reason |
| Kill switch | every algo of the account (firm: all) stops; starting one is refused while it's on |
| Restart | algos don't survive a restart: their leftover children are cancelled at start |

States: WORKING → PAUSED ⇄ WORKING → STOPPING → DONE (filled) / STOPPED / FAILED. Every
change is audited in the event log (`ALGO_STARTED`, `ALGO_PAUSED`, `ALGO_DONE`, …).

## Slicer (`algos/slicer.py`)
A big order as equal slices at regular times (time-sliced execution).

| Setting | Meaning |
|---|---|
| Slices, Every (s) | how many pieces and how far apart; whole lots, the remainder on the first slices |
| Price: join / cross | wait at the best price on our side (cheaper, may not fill) / take the other side (fills, pays the spread) |
| Limit | never buy above / sell below it; beyond it the slicer waits and says so |

- Unfilled slices follow the best price **on the slice clock**, not on every tick (a busy market
  would burn modifications). A slice modified 24 times is cancelled and its rest re-sent (brokers
  allow 25).
- A slice is never bigger than planned: refused or replaced quantity goes out in further slices
  after the plan ("catching up"), never as one big order. (Found by a paper run: risk refused
  ₹94 lakh slices and the first version tried to send everything left in one order.)
- Pause pulls the working slices; resume sends again at once.

## Spreader (`algos/spreader.py`)
Trade leg A against leg B at a spread level (A − B), e.g. a NIFTY calendar spread.

```
buy the spread:  quote A at level + B's best bid   ─► A fills ─► sell B at its bid − pay-up ticks
sell the spread: quote A at level + B's best ask   ─► A fills ─► buy  B at its ask + pay-up ticks
B moves ─► re-quote A (24 modifications, then cancel and re-send)
```

| Setting | Meaning |
|---|---|
| Leg B | the other contract (same lot size as A) |
| Spread (A − B) | the level, may be negative; the quote is rounded so the spread is never worse |
| Pay-up | ticks the hedge may pay beyond the best price; it never chases further |

- Hedges go out on **every** child update (a state change can report the fill before the FILL
  event), and only for what isn't covered yet (no double hedges).
- Pause and stop pull the quote but keep hedges working; a fill that crosses a cancel is still
  hedged. Hedges may be sent while paused or stopping (they reduce risk).
- Done only when both legs are complete. If it ends with anything unhedged it raises an
  **ALERT** ("UNHEDGED: 75 on NFO:NIFTY26OCTFUT … hedge it by hand"), in its reason and the audit.
- If the broker refuses 3 hedges in a row (e.g. a kill switch), it gives up and ends FAILED with
  that alert, instead of retrying forever.
- The screen shows legs filled, unhedged quantity, the spread actually traded and the legging time.

## Autospreader (`algos/autospreader.py`, #415)
The Spreader's rules for any of your spreads of 2 to 6 legs ([spreads.md](spreads.md)). It is
started from a spread (`POST /api/accounts/{id}/spreads/{spread}/orders`: side, spread lots,
spread price), never from the generic algo form (its family is "spread").

```
parent = the quoting leg Q: its side and quantity (spread lots · |Q's ratio| · Q's lot)
quote Q at (P − Σ others' price× · the price they'd hedge at) / Q's price×, rounded so the
      spread is never worse than P
Q fills f lots ─► every other leg: floor(f · |its ratio| / |Q's ratio|) lots at once,
                  at its best ± its own pay-up ticks
```

- The definition is copied when the algo starts: editing the spread later never changes a
  running algo. Only its owner can trade it, and only in whole spread lots.
- No quote while any leg is short of its hedge, has no price, or is more than 3 s older
  than the others.
- **Every order fits the account's per-order limits (#422)**: the lots limit and the value
  limit at that order's price (`RiskGate.max_order_qty`, given to every algo as
  `ctx.max_order_qty`, read again each time). A hedge bigger than that goes out at once in
  pieces (12 lots with a 10-lot limit: 10 + 2). The quote rests one piece at a time, in whole
  spread lots; the next piece is quoted once everything is hedged. A resting quote too big at
  its new price is pulled and quoted smaller, never modified into a refusal. If a leg can't
  fit even its smallest piece at its price, the algo fails at start and says why; if that
  happens to a hedge later (the limits changed), it says "UNHEDGED … one lot is over the
  per-order limit" and the unhedged alert follows.
- The screen shows the legs filled ("NIFTY26OCTFUT bought 75 · NIFTY26NOVFUT sold 150 · …"),
  the total unhedged and which legs are short, the spread traded and the legging time.
- **The spread's controls (#425):** a re-quote threshold, a maximum unhedged size, a hedge
  chase with its cap, and a hedge timeout with one ALERT ([spreads.md](spreads.md)).
- **Its actions:** "Hedge now" and "Flatten", previewed in words and done only as previewed.
  Any algo can offer actions: a strategy lists them (`actions`) and gives `preview` and
  `act`; the engine checks that the preview still holds before acting.
- **Paused or stopping:** the heartbeat calls the strategy's `on_watch`, so hedges are still
  retried and timed; a hedge sent then stays working (`keep_on_stop`).
- **After a restart (#426)** it comes back, see below.
- **Several quoting legs (#427):** each rests a quote; the lots left are shared out so all
  quotes together never overfill; every order carries its role (`algo_role`)
  ([spreads.md](spreads.md#4-trading-a-spread-415)).

## After a restart (#426)

```
algo starts ─► ALGO_STARTED in the audit trail, with its snapshot (ASE: the spread it copied)
server dies … starts again ─► orders rebuilt and checked with the broker (recovery, #206)
   └─► every ALGO_STARTED with no end (DONE, STOPPED, FAILED, LOST) whose type can be
       restored ─► AlgoEngine.restore: PAUSED, "restored after a restart: check it, then
       resume or stop", with its children; its quotes are cancelled, hedges keep working
   └─► one that can't be rebuilt (its type gone, its record broken) ─► ALGO_LOST, in words;
       its children are cancelled as orphans, as every other algo's are
trader resumes ─► it hedges what is short (a fill while we were down is hedged now, not
                  before), then quotes afresh;  or stops it (the unhedged alert if short)
```

- **Nothing is sent by itself after a restart.** A fill that came while the server was
  down is hedged only when the trader resumes (or uses Hedge now / Flatten, which work at
  once). Hedges working at the broker keep working; paper orders don't survive a restart.
- It works the spread it copied when it started, even if the spread was edited or deleted
  since. It comes back after every restart until it ends.
- An algo type is restorable when it says how (`AlgoType.restore`, from its parameters and
  `snapshot()`); today the ASE. Other algos don't survive a restart, as before (protection
  algos are flagged by the guard, #264).
- **Algo ids carry the run's start time** (`A<base36 ms>-1`): a new algo never takes the id
  of one from an earlier run, whose late order events would otherwise reach it.
- Checks: `tests/test_algo_restore.py`, `context/scripts/mutate-algo-restore.py`, and the
  crash drill `context/scripts/drill-algo-restart.py` on the demo.

## Execution algos (K9.1): iceberg, TWAP, VWAP, POV
Shared parts live in `algos/execution.py`: where to price a child (`touch`: join our side or
cross to the other, never beyond the limit) and keeping working children at that price
(`Chaser`: 24 modifications, then cancel; the quantity goes out again later). Each algo counts
children it has sent but the order manager hasn't registered yet (`ctx.sending`), so a fast
second event never sends a second order. (Found by the first iceberg test: two slices at once.)

**Iceberg** (`algos/iceberg.py`): only one slice is ever in the market, at the trader's limit.
A filled slice is replaced at once; a refused or cancelled one waits for the next heartbeat
(three refusals in a row fail the algo, as for every algo). "Vary size" moves each slice's
size by up to that share of the display size, in whole lots, so the refills are harder to spot.

**TWAP / VWAP** (`algos/schedule.py`): the schedule says how many lots should be filled by
now; every decision (the interval moved by up to ±20%, so it isn't a clock others can see):

| Situation | What it does |
|---|---|
| behind by more than "Catch up at" (share of the order) | crosses the spread for what's due, and moves resting children there |
| otherwise | rests what's due at our side's best price |
| window over, "cross" | takes the rest at the other side (never past the limit) |
| window over, "leave" | stops, the rest unfilled, and says how many lots |

- TWAP follows time; VWAP follows a **volume curve**: minute weights from 09:15 IST (data, not
  code). Today a typical NSE U-shape (busy open and close, quiet lunch); K11 can measure it
  from recorded days. A window started at the open fills more than half in its first half.
- A window running past 15:30 is cut at the close; a VWAP window outside market hours has no
  volume to follow, so it follows time. Both say so in the status.
- VWAP reports the market's VWAP over its own window, from the feed's day volume and average
  price (`(V1·P1 − V0·P0) / (V1 − V0)`), and our price against it in basis points (positive =
  worse for us, for buys and sells alike).

**POV** (`algos/pov.py`): every 2 s, the share × market volume since the start (our own fills
are part of it) less what's filled and working; whole lots, at most "Max per order" a child.
A quiet market means a slow algo: it never trades ahead of the volume to finish sooner.

The screen needed no change for any of them beyond one new setting kind (`minutes`) and
figure units by name (`…_pct`, `…_bps`, `…_at`). Specs now carry a `family` ("execution";
K9.2 adds "protection") for the screens to group by.

## Protection held by MockTrade (K9.2): trailing stop, OCO exit, bracket
`algos/protect.py`, family `"protection"` in the spec (screens group by it). Each one's
stop is **synthetic**: MockTrade watches the last traded price and sends the exit only when
the stop is touched. Nothing sits at the exchange for it before, so a stop and a target can
never both fill and turn an exit into a new position the other way.

| Algo | Parent side / qty | What it does |
|---|---|---|
| Trailing stop | the exit side (SELL protects a long) | stop = best price − trail (points or %), on the tick grid, moved only when a new best is seen; touched ─► exit |
| Target + stop (OCO) | the exit side | target rests as a limit; stop touched ─► cancel the target, **then** exit what is still open |
| Bracket | the entry side | entry (limit, or market); each entry fill gets a target for its quantity; stop touched ─► cancel the entry's rest and the targets, then exit what is open |

- "When touched": `market` (protected market order) or `limit` with a slack of N ticks past
  the stop. A gap through the stop exits at the market, or waits at the limit and says so.
- Exits never exceed what is open: the engine's main leg is now the parent's instrument
  **and side**, so a bracket's exits don't count as entry fills, and an OCO's exit can't go
  while its target is working (two layers: the algo waits for the cancel, and the engine's
  never-over-send rule refuses it).
- Pause or stop pulls every child: the screen shows the position as unprotected. A bracket
  that ends with a position open says `UNPROTECTED: n of …` in its reason and the audit.
- **Honest about restarts**: these are held by MockTrade, so they work only while the server
  runs (a broker GTT, K9.3, survives our outages). At start, any protection algo that was
  running when the server stopped raises an ERROR alert naming it, and is noted
  `ALGO_LOST` in the audit (`Platform.lost_protection`).
- **The flag lasts until resolved** (#264): the guard (`ops/protection.py`) writes each lost
  stop as an `UNPROTECTED` note, and its end as `PROTECTION_RESOLVED` (flat, covered again,
  or restored). Each start rebuilds the unresolved ones from these notes, so a second restart
  can't forget one.
- **Restore, never automatic** (#264): `GET /api/accounts/{a}/protection` lists the
  unprotected positions, each with what Restore would start or what stands in its way.
  `POST …/protection/restore {instrument_id, confirm}` is allowed only for someone who trades
  the account. It starts the same stop sized to what is still open (a bracket as an OCO at its
  levels), and only when that can't duplicate an exit. It is refused while:
  - the account is rebuilding;
  - an old order is working or unanswered;
  - another stop on the position is still there;
  - the position flipped;
  - the size is in doubt.

## The algo screen (`ui/src/widgets/algos/`, K6.4)

```
[New algo ▾]  Pause  Resume  Stop                                            Stop all (2)
▸ Algo  Type    Contract       Side  Qty  Filled  Avg        Working  State    Status
▾ A2    SPREAD  NIFTY26OCTFUT  BUY    75      75  25,010.00        0  Done     fully filled
    Leg B NIFTY26NOVFUT · Unhedged 0 · Traded spread −139.10 · Max legging 87 ms
    Spread (A − B) −50.00 · Pay-up 2
    09:14:24.699 D2610143 BUY  75 NIFTY26OCTFUT 25,099.10 Filled
    09:14:24.788 D2610144 SELL 75 NIFTY26NOVFUT 25,148.90 Filled
```

| API | |
|---|---|
| `GET /api/accounts/{a}/algo-specs` | what each algo needs (the form is drawn from it) |
| `GET /api/accounts/{a}/algos` | today's algos (loaded at sign-in, then kept live by the stream) |
| `POST /api/accounts/{a}/algos` | start: `{code, instrument_id, side, qty, product?, params}` → 201, a refusal → 422 |
| `POST /api/accounts/{a}/algos/{id}/pause\|resume\|stop` | → the algo; not allowed in its state → 409 |
| stream `{"type":"algo", account_id, kind, detail, algo}` | every change, never conflated |

- **Backend provides, frontend displays.** The form has one input per parameter, chosen by
  its kind (whole numbers, rupee prices, signed offsets, seconds, choices, flags, contract
  search). An unknown kind gets a plain text box and the server decides. A new algo needs no
  screen change.
- An algo's own figures (`extra`) are shown by name, formatted by their unit suffix:
  `…_paise` is a price and `…_ms` is milliseconds. A non-zero `unhedged` is shown in red.
  Settings use the labels from the algo's spec.
- Starting, pausing and resuming pass the same trading gate as orders. **Stop** stays allowed
  while trading is stopped, because getting out must always work, the same as cancel.
- Messages: started, done, stopped, failed and every **ALERT** (errors in red); routine
  updates appear only on this screen.
- In the default layout the screen is a tab next to the Order Book. Clicking a row sends its
  contract to the link group.
