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
