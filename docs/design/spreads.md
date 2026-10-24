# Spreads and the Autospreader

A **spread** is 2 to 6 contracts traded as one thing. Examples: a calendar (buy November,
sell December) or a butterfly (buy A, sell 2 B, buy C). The **Autospreader** defines spreads,
prices them live from their legs, and trades them as one order: it quotes one leg and hedges
the others the moment that leg fills.

Ayush asked for this on 24 Oct 2026. It is built in three parts:

| Part | Issue | What it adds | State |
| --- | --- | --- | --- |
| 1 | #414 | spread definitions, their live implied price, the Autospreader widget | done |
| 2 | #415 | multi-leg execution: quote one leg, hedge every other | done |
| 3 | #416 | the spread ladder: click to trade a spread; right-click "Autospreader settings…" | next |

Still to come, in #250 and #251:
- several quoting legs at once;
- a maximum unhedged exposure and timeouts;
- recovery after a restart;
- positions grouped by structure;
- historical spread charts.

## 1. A definition

| Per leg | Meaning |
| --- | --- |
| **ratio** | Lots of this leg per spread lot. Its sign is the side: buying the spread buys the + legs and sells the − legs. |
| **price ×** | How much this leg's price counts in the spread's price. It has the ratio's sign and is usually the ratio itself. |
| **quote** | The one leg that rests a quote. The others are hedged when it fills. |
| **pay-up** | How many ticks worse than the best price this leg's hedge may pay. |

```
NIFTY calendar        leg            ratio  price ×  quote  pay-up
                      NIFTY26NOVFUT   +1     +1       ●      2
                      NIFTY26DECFUT   −1     −1       ○      2
price = NOV − DEC     one spread lot = 1 lot of each (75 + 75)
```

The server checks every rule and says what is wrong in words:
- 2 to 6 legs, each a different, known, tradable contract that has not expired;
- ratios and multipliers are whole numbers from 1 to 50 (+ or −), with the same sign;
- exactly one quoting leg;
- pay-up is 0 to 50 ticks;
- the name is 1 to 40 characters;
- at most 50 spreads per user.

Definitions belong to one user (in one firm). Nobody else sees them, prices them or streams
them; the cross-firm leak test (`tests/test_workspaces.py`) covers every spread route.

## 2. The implied price

```
spread ask (buy it now)   = Σ price× · (the leg's ask if price× > 0, else its bid)
spread bid (sell it now)  = Σ price× · (the leg's bid if price× > 0, else its ask)
size, in spread lots      = min over legs of  floor(quantity at that price / (|ratio| · lot size))
tick                      = gcd of |price×| · leg tick
mark (the "LTP")          = Σ price× · leg last price
```

A worked calendar, with NOV 25,000.00 / 25,001.00 and DEC 25,150.00 / 25,152.00:
- buy the spread at 25,001.00 − 25,150.00 = −149.00;
- sell it at 25,000.00 − 25,152.00 = −152.00.

Honest by design:
- **Only the top level is implied.** Deeper levels of different legs don't trade together,
  so no depth is invented.
- A side has no price if any leg has no price there, or has less than one spread lot.
- **Stale legs:** legs whose prices are more than 3 s apart (by when they arrived) give no
  tradable price, only the mark (#267).
- Spread prices may be negative, as a calendar often is.

## 3. How it flows

```
Autospreader widget ── POST/PUT/DELETE /api/spreads ──► SpreadBook (data/spreads/<user hash>.json)
                                                             │ changed
a screen watches "SPREAD:<id>" ─► MarketData.on_demand ─► SpreadPricer ─► watch the legs
a leg ticks ─► SpreadPricer (a tap) ─► implied() ─► MarketData.on_derived("SPREAD:<id>" tick)
                                                             └─► its watchers (screens, stream)
```

- **One pricer per price source.** A spread is priced from its trader's own subscription
  (#395), like every other price.
- **Derived ticks are separate.** `on_derived` sends a derived tick to the spread's watchers
  only. The tick recorder, paper fills, charts, time and sales, movers and alerts never see
  it. Recording it would replay the spread twice: once from itself, once from its legs.
- **The spread as an instrument.**
  - `GET /api/instruments/SPREAD:<id>` (owner only) answers with `kind: "SPREAD"`, its tick,
    `lot_size: 1` (one spread lot) and its legs.
  - The stream accepts `SPREAD:<id>`, again for the owner only.
  - It is not yet `tradable`; that comes with #416.
- **Kept and erased like other personal data.**
  - Saved per user, written off the trading loop, and loaded at start.
  - In the backup (`spreads/`).
  - In `users export-user`, and removed by `users erase-user` ([data.md](data.md)).

## 4. Trading a spread (#415)

```
POST /api/accounts/{account}/spreads/{spread}/orders   {"side": "BUY", "lots": 2, "price_paise": -13000}
     └─► the ASE algo: parent = the quoting leg (its side and lots · |ratio| · lot size)
         quote it where the spread = the price, hedge every other leg the moment it fills
```

The server works out every leg's side and size; the screens send only the spread, its side,
lots and price. Every order the algo sends fits the account's per-order limits (lots and
value): bigger hedges go out in pieces at once, and the quote rests one piece at a time
(#422). With the default ₹50 lakh, 2 lots of a NIFTY future (about ₹37.5 lakh) fit one order. The algo's rules are in [algos.md](algos.md#autospreader-algosautospreaderpy-415).

## 5. Code

- `src/mocktrade/spreads/model.py`: definitions, checks, the implied price.
- `src/mocktrade/spreads/store.py`: `SpreadBook`, per user, on disk.
- `src/mocktrade/spreads/pricer.py`: live prices as derived ticks.
- `src/mocktrade/algos/autospreader.py`: the ASE algo (#415).
- `ui/src/widgets/ase/`: the Autospreader widget and its form model.
- Tests:
  - `tests/test_spreads.py` (hand-worked prices, rules, the book, the pricer);
  - `tests/test_spread_api.py` (routes, ownership, the stream, trading a spread);
  - `tests/test_autospreader.py` (the algo: quotes, hedges, stops, the paper broker);
  - `ui/src/widgets/ase/*.test.ts*`.
- Checks:
  - `context/scripts/mutate-spreads.py` (50/50), `context/scripts/mutate-autospreader.py` (34/34),
    `context/scripts/mutate-ase-pieces.py` (18/18, #422);
  - `context/scripts/e2e-autospreader.cjs` (real browser, 11/11).
