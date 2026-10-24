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
| 3 | #416 | the spread ladder: click to trade a spread; right-click "Autospreader settings…" | done |
| – | #424 | freeze quantities: every order fits the exchange's most per order | done |
| – | #425 | each spread's controls; "Hedge now" and "Flatten" on a running ASE | done |
| – | #426 | after a restart a working ASE comes back paused, with its orders | done |

Recovery after a restart (#426): a working Autospreader comes back paused, with its orders
([algos.md](algos.md#after-a-restart-426)).

Still to come, in #250 and #251 (one issue each, in this order):
- #427 several quoting legs at once;
- #428 positions grouped by structure;
- #429 historical spread charts.

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

**Controls (#425)**: how the Autospreader trades the spread, set in the same form. The server
publishes them (`GET /api/spreads/spec`: label, limits, default, help) and the form is drawn
from that list; an edit without controls keeps the spread's own.

| Control | Default | What it does |
| --- | --- | --- |
| **Re-quote threshold** | 1 tick | The quote moves only when its target has moved this many ticks (fewer of the 25 modifications spent). A move that would make the spread worse than its price is made at once. |
| **Max unhedged** | 0 (none) | A quote never carries more than this many spread lots, so one fill never leaves more than this unhedged. |
| **Chase** | 0 ticks | A working hedge follows the market to its new best ± pay-up, never more than this many ticks past its first price, and never back. |
| **Hedge timeout** | 5 s | A leg unhedged this long raises one ALERT, and the algo keeps saying "UNHEDGED 75 on NOV for 6 s: hedge now or flatten". |

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
  - It is `tradable`: a spread ladder trades it (§5).
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

**Actions on a running ASE (#425)**, on the algo screen. A click shows what it would do, in
the server's words; Confirm sends those words back and the server does exactly that, or
refuses with what it would do now (`GET`/`POST …/algos/{id}/actions/{name}`).
- **Hedge now:** every short leg crosses the market within the broker's protection. Working
  hedges are moved there; what nothing covers is sent there.
- **Flatten:** undo what is unhedged. Stop quoting and cancel every working order; once the
  cancels have landed (a fill they cross is counted), close each leg's part beyond the
  complete spread lots, crossing the market. The algo stops with whole spreads only.

Hedges, and a flatten's closing orders, keep working while the algo is paused or stopping,
and the heartbeat still drives them (the engine's `on_watch`). Before #425 a hedge sent while
paused was cancelled the moment the broker accepted it: fixed, with a test.

The server works out every leg's side and size; the screens send only the spread, its side,
lots and price. Every order the algo sends fits the account's per-order limits (lots and
value): bigger hedges go out in pieces at once, and the quote rests one piece at a time
(#422). With the default ₹50 lakh, 2 lots of a NIFTY future (about ₹37.5 lakh) fit one order. The algo's rules are in [algos.md](algos.md#autospreader-algosautospreaderpy-415).

## 5. The spread ladder (#416)

The normal ladder, opened on `SPREAD:<id>`, from the Autospreader's **Ladder** button.

```
Work | PIQ | Bids |  Price  | Asks | LTQ | VAP        prices: the spread's implied ones (may be negative)
     |     |   6  | −138.80 |      |     |            click Bids: buy the spread lots box at that price
     |     |      | −138.60 |  1   |     |            click Asks: sell; Ctrl+click joins the best price
```

- **A click** sends `POST /api/accounts/{account}/spreads/{id}/orders`, which starts the ASE
  algo (§4). There is no order type, TIF or product to choose: the server trades the legs.
- **Work** shows each running ASE algo on this spread at its spread price, with the spread lots
  not yet done. A click selects it and a second click cancels it, which stops the algo: its
  quote is pulled and its hedges finish. A click on another row moves it: the algo stops and
  the lots left start again at the new price.
- **Right-click:** "Autospreader settings…" first (the spread opens in the Autospreader, ready
  to edit), then the ladder's own settings and what every widget offers.
- The title is the spread's name; the position is shown in its legs (Positions).
- Checked in a real browser on the demo:
  - buying 1 spread lot at its ask filled both legs, +75 Oct and −75 Nov;
  - the spread traded at −138.60 for −138.50 asked, so better than asked;
  - a resting order showed in Work and was cancelled;
  - the right-click menu opened the settings.

## 6. Code

- `src/mocktrade/spreads/model.py`: definitions, checks, the implied price.
- `src/mocktrade/spreads/store.py`: `SpreadBook`, per user, on disk.
- `src/mocktrade/spreads/pricer.py`: live prices as derived ticks.
- `src/mocktrade/algos/autospreader.py`: the ASE algo (#415).
- `ui/src/widgets/ase/`: the Autospreader widget and its form model.
- `ui/src/widgets/ladder/`: the spread ladder (`spreadWorking` in `model.ts`, the spread path in
  `Ladder.tsx`).
- Tests:
  - `tests/test_spreads.py` (hand-worked prices, rules, the book, the pricer);
  - `tests/test_spread_api.py` (routes, ownership, the stream, trading a spread);
  - `tests/test_autospreader.py` (the algo: quotes, hedges, stops, the paper broker);
  - `ui/src/widgets/ase/*.test.ts*`, `ui/src/widgets/ladder/SpreadLadder.test.tsx`.
- Checks:
  - `context/scripts/mutate-spreads.py` (50/50), `context/scripts/mutate-autospreader.py` (34/34),
    `context/scripts/mutate-ase-pieces.py` (18/18, #422),
    `context/scripts/mutate-ase-controls.py` (32/32, #425);
  - `context/scripts/e2e-autospreader.cjs` (real browser, 11/11);
  - `context/scripts/e2e-spread-ladder.cjs` (real browser, 9/9), `context/scripts/mutate-spread-ladder.py` (22/22).
