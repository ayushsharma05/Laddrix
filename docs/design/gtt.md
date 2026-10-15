# GTT: orders held by the broker (K9.3, #201)

A GTT (good till triggered) is an order the **broker** keeps until the last price touches a
trigger; then the broker places a limit order. Because the broker holds it, it protects a
position even while MockTrade is down: the difference from MockTrade's own synthetic stops
(`algos/protect.py`, K9.2), which are smarter (trailing, OCO with cancel-first) but live only
while our server runs.

| Kind | Legs | Fires |
|---|---|---|
| single | one trigger and its limit order | when the last price touches the trigger (from either side) |
| OCO (`two-leg`) | a lower and an upper trigger, same side (both close the position) | the first one touched places its order; the other ends |

## Replaceable parts

```
screens ─► /api/accounts/{a}/gtt-spec?instrument_id=   (what the broker allows: drawn by the screens)
        ─► POST/PUT/DELETE /api/accounts/{a}/gtt[/{id}], GET /api/accounts/{a}/gtt
        ◄─ stream {"type": "gtt", ...} on every change
GttService (orders/gtt.py) ─► GttBook port (broker/base.py)
                                ├─ KiteBroker: /gtt/triggers (broker/kite/gtt.py)
                                └─ PaperBroker: simulated on live ticks, the way Kite behaves
```

A broker without GTTs simply doesn't implement `GttBook`: the account has no GTT service and
the API answers 404 "… doesn't hold GTT orders", so screens hide it.

## Rules (each a real failure)

- **The spec first.** Kinds, whole lots, the tick grid for trigger and price, each trigger at
  least 0.25% from the last price, an OCO's triggers on either side of the market and the
  same side for both legs, at most 250 active (Kite's limits, checked 15 Oct 2026; re-check
  before going live). Every problem is named in words, all at once.
- **Risk at the trigger.** Each leg goes through the normal risk checks *as if the price were
  at its trigger* (`RiskGate.admit_at`): a stop 8% below the market is fine at its trigger,
  but a GTT that would open a naked option short is refused like any order.
- **The broker places the order, so MockTrade adopts it.** Kite has no GTT push: the service
  reads the broker's GTT list every 5 s. A GTT that fired carries the broker's order id; the
  order manager **adopts** that order (`OrderManager.adopt`) under one of our client ids, and
  replays any update that came before. Its fills reach positions and the audit trail like
  any order of ours: never reported as someone else's order, never a position mismatch.
- **No clear answer is never retried.** A place that times out may have created the GTT: the
  trader is told to check the GTT list; the next read shows it (`GTT_FOUND`).
- **Audited**: `GTT_PLACED`, `GTT_MODIFIED`, `GTT_DELETED`, `GTT_TRIGGERED` (with the order id),
  `GTT_FOUND` (one placed outside MockTrade, or before a restart).
- **What a GTT can't do**: the risk checks run when it is placed, not when it fires (the
  broker places that order without asking us). Paper GTTs, like paper orders, live in memory:
  a restart forgets them; Kite's survive and are read back at start.

## Screens (K9.4, #202)

- **GTT Book** (`ui/src/widgets/gtt/`, a tab beside Algos): GTTs live from the stream,
  active first; New GTT draws its form from the spec (kinds and their labels, legs per kind,
  help, products, tick, lot); a two-leg kind shows "Stop (lower)" and "Target (upper)". The
  form starts each trigger 1% from the market and each limit five ticks past it, and says
  every problem in words before the click (the server checks again). Delete on an active one.
- **Ticket tabs**: Order | Algo | GTT. Algo and GTT reuse the same forms (`StartForm`,
  `GttForm`) with the ticket's contract, side and lots. The GTT tab appears only when the
  broker publishes a GTT spec.
- **Algo screen**: the picker is grouped by the spec's `family`; protection algos carry a
  "held" tag (held by MockTrade, unlike a GTT).
