# MockTrade: the guide (start here)

Written for Ayush on 16 Oct 2026. Plain words, no assumed knowledge. It explains what the
project is, how the parts fit, how one order travels through the code, how each safety system
works, and what is left. Read it with the code open beside you.

If a word is new, look it up in [design/glossary.md](design/glossary.md).

---

## 0. A five-day plan (Fri to Tue, before we restart on Wednesday)

One hour a day is enough. Each day has something to read and something to do.

| Day | Read | Do |
| --- | --- | --- |
| **1 · Fri** | Sections 1–3 of this guide | Run the demo (section 12). Log in, open a ladder, buy 75 NIFTY futures, watch the order book and positions change. |
| **2 · Sat** | Section 4 (one order's life), then `src/mocktrade/orders/manager.py`, only `submit` and `apply` | Place an order, then open its audit trail (double-click it in the order book). Match each line with section 4. |
| **3 · Sun** | Section 5 (safety), then [design/order-life.md](design/order-life.md) | Press STOP, then Flatten in the red band. Set an MTM loss limit of ₹1 with auto-close and watch it close the position. |
| **4 · Mon** | Sections 6–8 (screens, data, tests) | Run the tests (section 12). Break one line in `risk/checks.py` on purpose, run the tests, and see them fail. Then undo it. |
| **5 · Tue** | Sections 9–11 (how we work, what's left, your steps) and [runbooks/go-live.md](runbooks/go-live.md) | Write down your questions. We start Wednesday with them. |

---

## 1. What MockTrade is

A trading platform for the Indian market (NSE, BSE, and their futures and options), built as
its own product. It talks to **Zerodha Kite Connect**: Kite's WebSocket for live prices,
Kite's REST API for orders.

It also has a **paper broker**: a pretend broker that fills your orders against the real live
prices, so everything can be tried without money.

```
                 ┌──────────────── MockTrade server (Python) ────────────────┐
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
| Folder | `src/mocktrade/` | `ui/src/` |
| Starts with | `python -m mocktrade.tools.serve` (real) or `python -m mocktrade.tools.demo` (practice) | built by Vite into `ui/dist`, served by the server |
| Tests | `tests/` (pytest, about 1,250) | `*.test.tsx` next to each file (vitest, about 400) |

The screens talk to the server in two ways:
- **REST** (`/api/...`): asking for something or doing something, such as placing an order.
- **The stream** (a WebSocket at `/ws`): the server pushes every change (prices, order updates,
  positions, alerts) as it happens.

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
| `server/` | The web layer: login (password + authenticator code), the REST API, the stream, admin. | `app.py` (long: use search) |
| `tools/` | Command-line programs: `serve`, `demo`, `users`, `backup`, `restore`, `loadtest`. | `demo.py` |
| `platform.py` | **Wires everything together** for each account: broker, order manager, risk, positions, algos and the rest. | the `add_account` method |
| `recovery.py` | After a restart: rebuild orders and positions before trading may start again. | the top comment |

---

## 4. One order's life, traced through the code

You click **Buy 75 NIFTY** on the ladder.

```
screen  ui/src/widgets/ladder → api/rest.ts  POST /api/accounts/acc-paper/orders
          │
server  server/app.py  place(): who are you? may you trade this account?
          │            builds an OrderRequest (domain/orders.py); the user comes from the
          │            session, never from the request body
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
| **Lost stops** | A stop held by MockTrade that stops running while its position is open (restart, pause, failure) raises an UNPROTECTED band. | `ops/protection.py` |
| **Backups** | All state copied with checksums; restore keeps what it replaces. | `tools/backup.py`, `tools/restore.py` |

**One important limit to remember:** stops held by MockTrade (trailing stop, OCO, bracket)
work only while the server runs. For protection that survives an outage, use a stop-loss
order or a GTT held by the broker.

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

The design rules for the screens (colours, keys, every widget at any size) are in
[design/ui.md](design/ui.md).

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

---

## 8. How we know it works

- **Unit tests:** `python3 -m pytest -q tests` (server) and `cd ui && npx vitest run` (screens).
  CI on GitHub runs them for every PR.
- **Mutation checks** (`context/scripts/mutate-*.py`): break one rule in the code at a time and
  check that a test fails. If a test still passes with the rule broken, a test is missing.
  Every safety change has one, and all their mutations are caught.
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

## 10. Where things stand (16 Oct 2026)

**Done:**
- Everything up to v1.0.0-rc.1 (the release candidate).
- From the outside review's core list, six safety fixes:
  - **#258:** positions kept separately per product;
  - **#259:** unanswered orders;
  - **#260:** changes and cancels in doubt;
  - **#261:** coordinated exits;
  - **#262:** STOP / cancel / flatten;
  - **#263:** loss-limit closes survive restarts.
- **#269:** a path fix.

**Paused until Wednesday** (nothing runs with real money before then, so waiting is safe):

| Issue | What | Why it matters |
| --- | --- | --- |
| #264 | Lost stops: a flag that survives every restart, and a guarded "Restore stop" | Half done, saved on branch `h-264-protection` (not merged). |
| #265 | Order intents written to disk before sending; stop safely if the disk fails | A crash at the wrong moment must not lose what was sent. |
| #267 | Never trade on old prices; keep execution prices separate from screen updates | Old prices mean wrong fills and wrong risk. |
| #270 | One user must never see another user's alerts or research files | Privacy between users. |
| #266, #268, #271–#277 | Day rollover, screens waiting for a full sync, webhook checks, backups, task supervision, capacity, exact charges, broker-held triggers, data labels | Important, less urgent. |
| #287, #290, #291 | Logs and status page, F&O expiry day, compliance (SEBI algo id) | Before real use. |
| #279, #278 | Security review; the decision on who pays for live data | Need your decisions. |
| #241 (#242–#255), #297–#322 | New features | After all of the above. |

---

## 11. The steps only you can do (slowly, when you're ready)

None of these is urgent, and none happens without you.

1. **Your v1 acceptance on issue #240:** you try the platform (the demo is enough) and say on
   #240 whether it does what you wanted. It's just your opinion, written down.
2. **The live go-live run** ([runbooks/go-live.md](runbooks/go-live.md)): 28 boxes, done with
   your own Zerodha login and the smallest possible quantity. It needs your Kite API key and the
   server's static IP. **No live order is ever placed without you saying so.**
3. **Tagging v1.0.0** ([runbooks/release.md](runbooks/release.md)): one command, after the
   two steps above.

---

## 12. Running it yourself

```bash
# the demo: a paper account on simulated prices, everything on your machine
cd ui && npm install && npm run build && cd ..
python3 -m mocktrade.tools.demo --ui ui/dist          # then open http://localhost:8000
python3 -m mocktrade.tools.demo --code                # in another terminal: the login code
#   username demo, password demo-password-123, the code from the line above

# the tests
python3 -m pytest -q tests
cd ui && npx vitest run
```

**Where to look next:**
- [design/README.md](design/README.md) lists every design document.
- [design/domain-model.md](design/domain-model.md) explains the main objects and how they
  relate.
- [design/order-life.md](design/order-life.md) has the order journeys with diagrams.
- [runbooks/README.md](runbooks/README.md) has what to do when.
