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
- **AlgoType**: spec + factory + the exchange's algo id once empanelled (SEBI framework). Until
  then children carry no exchange id; they are marked as algo orders by `parent_id`.

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
