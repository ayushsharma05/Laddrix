# The guide on one page

The whole guide in one page: what Laddrix is, how the parts fit, how one order travels
through the code, how each safety system works. Read it with the code open beside you. For
the slower version, with pictures and from zero, read the [chapters](README.md).

If a word is new, look it up in [the glossary](../design/glossary.md).

---

## 1. What Laddrix is

A trading platform for the Indian market (NSE, BSE, and their futures and options), built as
its own product. It talks to **Zerodha Kite Connect**: Kite's WebSocket for live prices,
Kite's REST API for orders.

It also has a **paper broker**: a pretend broker that fills your orders against the real live
prices, so everything can be tried without money.

```
                 ┌──────────────── Laddrix server (Python) ────────────────┐
 live prices ───►│ market data ──► paper broker (fills against live prices)   │
 (Kite WebSocket)│      │                                                    │
                 │      ▼                                                    │
 your browser ◄─►│ API + stream ◄─► order manager ──► risk checks ──► broker ─┼──► Kite (real orders)
 (React screens) │                      │                          (paper or │     or paper
                 │                      ▼                           Kite)    │
                 │        positions & P&L, audit log (events.db), alerts    │
                 └────────────────────────────────────────────────────────────┘
```

**Two rules shape all the code:**

1. **Every part can be replaced.** The broker, prices, charges and storage each sit behind a
   small interface. Swapping paper for Kite changes one object, not the code around it.
2. **The backend provides, the frontend displays.** The server decides and publishes what is
   allowed: order types, lot sizes, cut-off times, why something was refused. The screens
   never hard-code a market rule; they show what the server says.

---

## 2. The two halves

| | Server | Screens |
| --- | --- | --- |
| Language | Python 3.13 | TypeScript + React |
| Folder | `src/laddrix/` | `ui/src/` |
| Starts with | `python -m laddrix.tools.serve` (real) or `python -m laddrix.tools.demo` (practice) | built by Vite into `ui/dist`, served by the server |
| Tests | `tests/` (pytest, about 1,250) | `*.test.tsx` next to each file (vitest, about 400) |

The screens talk to the server in two ways:
- **REST** (`/api/...`): asking for something or doing something, such as placing an order.
- **The stream** (a WebSocket at `/ws`): the server pushes every change (prices, order updates,
  positions, alerts) as it happens.

```
   screen                                server
     │  REST: "place this order"  ───────►  │   you ask, it answers once
     │  ◄───────  "MT7, PENDING"            │
     │                                      │
     │  stream (always open)                │   it tells you, whenever
     │  ◄───────  price 25,010.00           │   anything changes
     │  ◄───────  MT7 FILLED                │
     │  ◄───────  position +75, P&L ₹0      │
```

**Logging in** (`server/routes/session.py`, `server/security.py`):

```
username + password + 6-digit code from your authenticator app
        │
        ▼
 all three right? ──no──► "wrong username, password or code" (the same words for any
        │                  mistake, so a guesser learns nothing); 5 misses → locked 15 min
       yes
        ▼
 a session cookie (secure, expires after 2 h idle or 12 h in all)
        │
        ▼
 every request after: who is this? may they see / trade this account?
```

There is also a desktop app (`ui/desktop/`, Electron), which wraps the same screens in a window.

---

## 3. A tour of the server, folder by folder

Read the first lines of each file: every file starts with a comment saying what it is for,
often with a small drawing.

| Folder | What it does, in one line | Read first |
| --- | --- | --- |
| `domain/` | The basic things: money in paise, instruments, orders, ticks. No logic, just shapes. | `orders.py`, `money.py` |
| `refdata/` | The list of instruments (what can be traded, lot sizes, tick sizes). | `store.py` |
| `marketdata/` | Prices: holds the latest tick of each instrument and hands it to whoever listens. Also the simulator the demo uses. | `service.py` |
| `broker/` | The broker interface (`base.py`) and its versions: `paper.py` (pretend), `fake.py` (for tests), `kite/` (the real one). | `base.py`, then `paper.py` |
| `broker/kite/` | Everything Kite: login, the binary price decoder, the WebSocket, orders, GTT. | `orders.py` |
| `orders/` | **The heart.** The order manager tracks every order from click to fill. `gate.py` chains the checks an order must pass. | `manager.py` |
| `risk/` | Every guard: limits, the kill switch, daily loss, MTM limits, square-off, the exit desk, flatten, throttle, margin. | `checks.py` |
| `positions/` | The position book (quantity, average price, P&L, from fills) and the reconciler that compares it with the broker's own book. | `book.py` |
| `algos/` | Algorithms that send orders for you: slicer, iceberg, POV, spread, and protection (trailing stop, OCO, bracket). | `engine.py`, `protect.py` |
| `options/` | Option pricing (Greeks), the option chain, multi-leg strategies. | `chain.py` |
| `charges/` | Brokerage, taxes and fees for each fill. | `model.py` |
| `charts/` | Candles for charts. | `candles.py` |
| `alerts/` | Price alerts the trader sets, and webhooks. | `engine.py` |
| `portfolio/` | Funds, holdings and the tradebook, as the broker reports them. | `service.py` |
| `reports/` | Daily reports (P&L, trades), CSV and PDF. | `daily.py` |
| `research/` | Recording ticks, replaying a recorded day, backtests. | `replay.py` |
| `ops/` | Running it: operational alerts (the bands), the scheduler, day guards, the lost-stop guard. | `alerts.py`, `protection.py` |
| `store/` | The audit log: every order event and note goes into `events.db` (SQLite), written in the background. | `events.py` |
| `server/` | The web layer: login (password + authenticator code), the REST API, the stream, admin. | `routes/trading.py` (orders), `context.py` (the checks) |
| `tools/` | Command-line programs: `serve`, `demo`, `users`, `backup`, `restore`, `loadtest`. | `demo.py` |
| `platform.py` | **Wires everything together** for each account: broker, order manager, risk, positions, algos and the rest. | the `add_account` method |
| `recovery.py` | After a restart: rebuild orders and positions before trading may start again. | the top comment |

---

## 4. One order's life, traced through the code

You click **Buy 75 NIFTY** on the ladder.

```
screen  ui/src/widgets/ladder → api/rest.ts  POST /api/accounts/acc-paper/orders
          │
server  server/routes/trading.py  place(): who are you? may you trade this account?
          │                       builds an OrderRequest (domain/orders.py); the user
          │                       comes from the session, never from the request body
          ▼
        orders/manager.py  OrderManager.submit
          │  1. gives it an id (MT1…) and records SUBMITTED (audit log)
          │  2. the gates (orders/gate.py), in order:
          │       risk     risk/checks.py   kill switch? limits? price band? lot size?
          │                                 daily loss? too many open orders? double click?
          │                                 a close that would close too much?
          │       margin   risk/margin.py   enough funds? (an exit never needs margin)
          │       throttle risk/throttle.py at most 8 orders a second (Kite allows 10)
          │     any "no" → REJECTED, with the reason in plain words
          │  3. broker.place(request) → the broker's order id
          │       clear answer   → SENT
          │       refusal        → REJECTED, with the broker's reason
          │       no answer      → UNCONFIRMED: never guessed, never sent again blindly
          ▼
broker  paper.py fills against live prices / Kite fills at the exchange
          │  updates come back: OPEN → PARTIAL → FILLED
          ▼
        OrderManager.apply(update)  → events: FILLED, with the fill
          │                        (the order manager tells everyone who subscribed)
          ├──► positions/book.py      quantity, average price, P&L
          ├──► charges/service.py     the fill's charges
          ├──► store/events.py        written to events.db (the audit trail)
          ├──► algos/engine.py        an algo that owns the order reacts
          ├──► risk/mtm.py, ...       loss limits look at the new P&L
          └──► server/stream.py       pushed to every open screen
                   │
screen  ui/src/api/stream.ts → state/store.ts → the order book, positions and ladder redraw
```

**Order states** (`domain/orders.py`): PENDING → OPEN → PARTIAL → FILLED, or CANCELLED or
REJECTED. Separately, **pending** says what is on its way and not yet answered: UNCONFIRMED
(the place got no answer), MODIFY (a change), CANCEL (a cancel).

**The order states as a picture:**

```
             ┌──────────► REJECTED   (a check said no, or the broker said no)
             │
 PENDING ────┼──────────► OPEN ──────► PARTIAL ──────► FILLED
 (sent, not  │             │              │
  answered)  │             └──────┬───────┘
             │                    ▼
             └──────────────► CANCELLED  (you, STOP, or the exchange cancelled it)

 FILLED, CANCELLED and REJECTED are final: nothing changes them after that.
```

**The gates, as questions** (`orders/gate.py`, `risk/checks.py`):

```
new order
   │
   ▼
 trading stopped (STOP)? ────yes──► refused  (except a forced close by the exit desk
   │ no                                       that can only reduce: see section 5)
   ▼
 right lot size? right tick? price within the band? ──no──► refused, saying which
   │ yes
   ▼
 too big? too much value? position too large? ──yes──► refused
   │ no
   ▼
 past the daily loss limit and NOT closing? ──yes──► refused
   │ no
   ▼
 the same order less than 1 s ago (a double click)? ──yes──► refused
   │ no
   ▼
 a close that would close more than is open? ──yes──► refused
   │ no
   ▼
 enough margin (funds)? ──no──► refused          (an exit never needs margin)
   │ yes
   ▼
 throttle: wait for a free slot (max 8 a second)
   │
   ▼
 sent to the broker
```

**When the broker doesn't answer** (`orders/manager.py`, #259):

```
 place ──► broker ... silence (timeout, network cut)
               │
               ▼
     UNCONFIRMED: maybe it's at the exchange, maybe not.
     Its risk still counts. It is NEVER sent again blindly
     (that could make two orders).
               │
               ▼
     ask the broker for its order list, again and again (2 s, 5 s, 10 s … ~3 min)
               │
     ┌─────────┴────────────────────────┐
     ▼                                  ▼
 the order is in the list          not in 3 lists over at least 60 s
     │                                  │
     ▼                                  ▼
 settled: OPEN / FILLED …          REJECTED: "the broker never received this order",
 (one order, never two)            with the times of the checks as evidence

 still unclear after ~3 min? ──► a message: check the broker's own order book,
                                  then press "Not placed?" (two clicks) to settle it
```

---

## 4b. How prices flow

```
 Kite WebSocket (binary packets, many per second)
        │
        ▼
 broker/kite/decoder.py   bytes ──► Tick (price, best bids and asks, volume)
        │
        ▼
 marketdata/service.py    keeps the latest tick of every instrument, and tells every listener
        │
        ├──► paper broker      fills resting paper orders that the new price reaches
        ├──► algos             a trailing stop moves; an OCO checks its levels
        ├──► positions         unrealised P&L moves (mark to market)
        ├──► MTM limits        loss limit reached?
        ├──► alerts            price alert reached?
        ├──► charts, recorder  candles; ticks saved for replay
        └──► stream            to the screens (in a busy market it may skip in-between
                               prices, so your browser isn't flooded)

 the demo has no Kite: marketdata/simulator.py makes believable prices instead
```

---

## 5. The safety systems, in plain words

Real money means anything that can go wrong eventually will. Each item below exists because of
a real failure, and each has tests that break on purpose to prove they work.

| What | What it does | Where |
| --- | --- | --- |
| **Risk limits** | Size, value, position, open-order and price-band limits on every order, set by an admin. | `risk/checks.py`, `risk/limits.py` |
| **Daily loss limit** | Past the limit, only closing trades are allowed; at the limit it engages the kill switch by itself. | `risk/loss_watch.py` |
| **STOP (kill switch)** | Blocks every new order and change, cancels working orders and stops algos. **It does not close positions.** The red band says exactly what it did. | `risk/kill_switch.py` |
| **Flatten** | Closes positions on purpose, even while STOP is on, using only orders that can't reverse a position. It refuses a position whose size the broker disagrees with. Two clicks. | `risk/flatten.py` |
| **Reduce-only closes** | Every closing order (from loss limits, square-off or stop algos) is marked: the risk check refuses it if it would close more than is open, so two exits can never turn a long into a short. | `risk/checks.py` |
| **The exit desk** | Every forced close goes through it: it stops the algos on that position, cancels resting closing orders, then closes only what's left. | `risk/closing.py` |
| **MTM limits** | Your own loss limits per position and for the account: an alert, or an automatic close. Each firing is saved and followed until the position is really closed; a blocked close shows **Retry**. | `risk/mtm.py` |
| **Square-off** | Before the broker closes your intraday (MIS) positions at 15:20/15:25, a warning, and one confirmed click to close them yourself. | `risk/squareoff.py` |
| **Unanswered orders** | An order with no answer stays UNCONFIRMED (its risk still counted) until the broker's own order list settles it; it is rejected only with real evidence (3 lists over 60 s). | `orders/manager.py` |
| **Changes and cancels in doubt** | A change with no answer counts its larger size; a cancel with no answer stays "on its way" and can be pressed again. | `orders/manager.py` |
| **Reconcile** | Every few minutes, our positions are compared with the broker's. A difference raises a band. | `positions/reconcile.py` |
| **Recovery after a restart** | Trading stays shut until orders and positions are rebuilt from the audit log and the broker. | `recovery.py` |
| **Lost stops** | A stop held by Laddrix that stops running while its position is open (restart, pause, failure) raises an UNPROTECTED band. | `ops/protection.py` |
| **Backups** | All state copied with checksums; restore keeps what it replaces. | `tools/backup.py`, `tools/restore.py` |

**One important limit to remember:** stops held by Laddrix (trailing stop, OCO, bracket)
work only while the server runs. For protection that survives an outage, use a stop-loss
order or a GTT held by the broker.

### STOP, cancel and Flatten: three different buttons

```
 STOP                          cancel (one order)           Flatten
  │                              │                            │
  ▼                              ▼                            ▼
 block every new order         that order only              close open positions
 cancel every working order    always allowed               allowed even while STOPPED
 stop every algo                                            only orders that can't reverse
  │                                                         refuses a size it isn't sure of
  ▼                                                           │
 positions STAY OPEN  ──── want them closed too? ──────────►  two clicks in the red band
 the red band says exactly what happened:
 "2 orders asked to cancel · 1 algo stopped · positions kept: NIFTY MIS +150"
```

### The exit desk: closing one position safely

Used by MTM auto-close, square-off and Flatten (`risk/closing.py`).

```
 "close NIFTY MIS"
        │
        ▼
 take this position's lock ── another close of it already running? ──► wait for it,
        │                                                                then see what's left
        ▼
 stop the algos on this position (a stop algo could sell at the same moment)
        │
        ▼
 cancel resting orders that would also close it (your target sell, for example)
 and wait up to 3 s for those cancels
        │
        ▼
 read the position NOW (fills meanwhile are already in it)
        │
        ▼
 minus what other closing orders still own (a cancel that didn't take, an unanswered order)
        │
        ▼
 send closing orders for the rest only, marked reduce-only
        │
        ▼
 report: "closing +150: sent MT9; cancelled MT4; stopped algos A1; 0 left to others"
```

**Why reduce-only matters:** you are long 150, and a stop algo and a loss limit both fire.

```
 without it:  stop sells 150  +  loss limit sells 150  =  you are now SHORT 150  ✗
 with it:     stop sells 150  →  the loss limit's sell is refused:
              "would close more than is open: 150 open, 150 already being closed"  ✓
```

### MTM loss limits: from "limit reached" to "closed"

```
 P&L of NIFTY MIS ≤ −₹1,000 (your limit)
        │
        ▼
 saved to disk first: "fired today" + a breach record
        │
 auto-close on? ──no──► ALERTED: a red band and a message; you decide
        │ yes
        ▼
 REQUESTED ── the exit desk closes ──► orders working? ──► WORKING
                                          │
              ┌───────────────────────────┼──────────────────────────────┐
              ▼                           ▼                              ▼
 position 0 and the broker      nothing working but the       an order the broker hasn't
 agrees on the size             position is still open        answered, or the broker
              │                 (refused, failed, restart)    disagrees on the size
              ▼                           ▼                              ▼
         COMPLETED                     BLOCKED                        UNKNOWN
                              red band + Retry (two clicks)   red band; check the broker;
                              never retried by itself         never retried while unknown

 after a restart: nothing is sent again by itself; the record says where it stood
```

### Square-off: intraday positions before the broker closes them

```
 15:00 ─────────────── 15:20 (equity) ── 15:25 (F&O) ───────► time (India)
   │                        │                 │
   ▼                        ▼                 ▼
 warning band every      the broker closes your MIS positions itself,
 minute: "2 MIS          at its own price, for a fee
 positions open,
 the broker squares
 off in 20 min"
   │
   ▼
 [Square off MIS (2)] → [Confirm] → the exit desk closes each one; overnight (NRML) stays
```

### After a restart: nothing trades until the state is rebuilt

```
 server starts
     │
     ▼
 trading is SHUT for this account (recovery.py)
     │
     ▼
 rebuild orders from the audit log (events.db)
     │
     ▼
 ask the broker: its order list and its positions
     │
     ▼
 apply what happened while we were down (fills, cancels)
     │
     ├── unanswered orders ──► followed up (as in section 4)
     ├── algos ──► they don't survive a restart: their leftover orders are cancelled,
     │             and a lost stop on an open position raises an UNPROTECTED band
     ▼
 positions match? ──no──► a "position differs" band: check before trading it
     │ yes
     ▼
 trading OPEN again
```

### Reconcile: are we and the broker saying the same thing?

```
 regularly, on a timer
     │
     ▼
 our positions (from fills)   vs   the broker's positions
     │
 same? ──yes──► nothing to show
     │ no
     ▼
 check again soon (it may just be a fill on its way)
     │
 still different? ──► band: "NIFTY MIS: Laddrix +75, broker +150 (a missed fill, or a
                       trade made outside Laddrix)"; Flatten refuses that position
```

### A lost stop

```
 a stop algo protects NIFTY +75
        │
 server restarts / you pause it / it fails
        │
        ▼
 position still open? ──no──► nothing to say
        │ yes
        ▼
 covered some other way? (another stop algo, a stop-loss order, a GTT) ──yes──► fine
        │ no
        ▼
 band: "UNPROTECTED: 75 NIFTY (long) is open with no stop: its TRAILING_STOP (A1) …
        Start a stop for it, or close it."   (stays until closed or covered)
```

---

## 6. The screens

| Folder | What it is |
| --- | --- |
| `ui/src/shell/` | The frame: login, toolbar, STOP, the alert bands at the top, themes. |
| `ui/src/workspace/` | The dockable layout: widgets in tabs and panes, saved layouts, pop-out windows. |
| `ui/src/widgets/` | One folder per widget: `ladder`, `ticket`, `books` (orders, positions, audit trail, messages), `chart`, `chain` (options), `strategy`, `algos`, `gtt`, `mtm`, `alerts`, `market`, `portfolio`, `reports`, `research`, `admin`, `keys`. |
| `ui/src/state/store.ts` | One store holds everything the screens know: orders, positions, prices, alerts. The stream fills it, and widgets read from it. |
| `ui/src/api/` | `rest.ts` (requests) and `stream.ts` (the live WebSocket, with reconnects). |
| `ui/src/lib/format.ts` | How money, prices and quantities are shown. |

**How a screen gets its data:**

```
 server ──stream──► api/stream.ts ──► state/store.ts (one place for everything)
                                          │
                       ┌──────────────────┼───────────────────┐
                       ▼                  ▼                   ▼
                    ladder            order book          positions …   (each widget reads
                       │                                                 only what it needs)
                       ▼
            you click ──► api/rest.ts ──► server ──► the answer comes back on the stream
```

**Linked widgets:** widgets with the same colour link follow each other. Pick an instrument in
the watchlist, and the pink ladder, chart and ticket all switch to it.

The design rules for the screens (colours, keys, every widget at any size) are in
[design/ui.md](../design/ui.md).

---

## 7. Data on disk

Everything lives in one data folder (`data/demo/` for the demo, `/data` on the server):

| File or folder | What it holds |
| --- | --- |
| `users.db` | Users, password hashes, authenticator secrets (encrypted), accounts, settings. |
| `events.db` | The audit trail: every order event, fill, kill switch and note, in order. Recovery rebuilds from it. |
| `rules/` | MTM limits (`mtm-<account>.json`) and what they did today (`…-state.json`). |
| `ticks/` | Recorded prices, one folder per day, for replay and research. |
| `reports/`, `alerts/`, `candles/`, `backtests/` | What those features keep. |
| `backups/` | Backups, each with a `manifest.json` of checksums. |

Secrets (the Kite API key, the server's secret key) live in `deploy/.env` on the server and are
never committed.

**Backup and restore** (`tools/backup.py`, `tools/restore.py`):

```
 backup:   copy users.db, events.db (safely, while running) + rules, reports, alerts …
           ──► backups/<time>/  + manifest.json (a checksum of every file)

 restore:  check every checksum ── any wrong? ──► stop, change nothing
               │ all good
               ▼
           move today's data aside to .before-restore-<time>/ (never deleted)
               │
               ▼
           put the backup in its place
```

**Deploying a new version** (`deploy/deploy.sh`):

```
 market hours (09:00–15:45 on a trading day)? ──yes──► refused (unless --emergency, logged)
        │ no
        ▼
 backup ──► keep the running version as "previous" ──► build and start the new one
        │
        ▼
 healthy, and says the right version? ──no──► roll back to "previous" by itself
        │ yes
        ▼
 done; every step in /var/log/laddrix-deploy.log
```

---

## 8. How we know it works

- **Unit tests:** `python3 -m pytest -q tests` (server) and `cd ui && npx vitest run` (screens).
  CI on GitHub runs them for every PR.
- **Mutation checks** (`context/scripts/mutate-*.py`): break one rule in the code at a time and
  check that a test fails. If a test still passes with the rule broken, a test is missing.
  Every safety change has one, and all their mutations are caught.

```
 a rule in the code:   if qty > closable - taken:  refuse
        │
 the script changes it to:   if qty > closable:  refuse     (a deliberate bug)
        │
        ▼
 run the tests ──► a test fails?  ✓ good: the tests guard this rule
                   all pass?      ✗ a test is missing: write it, then try again
        │
 put the line back; next deliberate bug
```
- **Real checks** (`context/scripts/e2e-*`): a real browser or a real demo run, with results
  saved in `context/evidence/`.
- **Drills**: crash-and-restart, backup-and-restore, deploy-and-rollback, each rehearsed for
  real and recorded.

---

## 9. How work is done

```
an issue (what and why) ─► a branch from main ─► code + tests + docs + context
   ─► a PR saying "Closes #N" ─► CI green ─► merged ─► check main is still green
```

- **`context/`** is the project's memory: `status.md` (where things are), `conversation-log.md`
  (what was decided and why, by date), `images/` (diagrams), `scripts/` (checks).
- **`docs/design/`** explains each part; **`docs/runbooks/`** says what to do when something
  happens (morning routine, incidents, releases, going live).

---

## 10. Where things stand, and what is next

These change every week, so they live where they are kept up to date:
- **Where things are, and the next piece of work:** [context/status.md](../../context/status.md).
- **What changed, version by version:** the [changelog](../../CHANGELOG.md).
- **What is planned:** the [roadmap](../roadmap/README.md) and the feature issues on GitHub.

---

## 11. The steps only you can do

They are one GitHub issue, "Ayush's to-do list", in order: learn it, run it, accept v1, ask
Zerodha, set up the live server, the go-live run, an independent security review. Chapter 9
explains each: [09-work-next-your-steps.md](09-work-next-your-steps.md). **No live order is
ever placed without you saying so.**

---

## 12. Running it yourself

```bash
make setup     # once
make demo      # http://localhost:8000: user demo, password demo-password-123
make code      # in a second terminal: the login code
make check     # everything CI checks
```

Every command, one by one, and what to do when something goes wrong:
[running-locally.md](../running-locally.md).

**Where to look next:**
- [design/README.md](../design/README.md) lists every design document.
- [design/domain-model.md](../design/domain-model.md) explains the main objects and how they
  relate.
- [design/order-life.md](../design/order-life.md) has the order journeys with diagrams.
- [runbooks/README.md](../runbooks/README.md) has what to do when.
