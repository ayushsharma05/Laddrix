# MockTrade UI: the terminal

The screens are a **trading terminal**, not a website: many small windows (widgets) in one
workspace, dense grids, click-to-trade ladders, fixed-size text. Convenience for the trader
comes first: the common action is one click, the dangerous one is never one accidental click.

The visual reference is the "MockTrade UI design" canvas (8 boards: ladder, design system,
workspace, order ticket and messages, stopped and alerts, sign-in and session start, audit
trail, phone). It follows patterns common to professional trading terminals (price ladders,
dockable widgets, audit trails); it copies no firm's screens or names.

## 1. Rules for every screen

- **Text never scales with the window.** 13 px body, 11–12 px headers, 22 px ladder net change.
  Smaller windows show less, never smaller text.
- **Numbers** are tabular (digits line up) and grouped the Indian way: ₹56,27,250.00.
- **Buy is blue, sell is red**, in depth, Work cells, text and buttons. Gains/losses also carry a
  sign (+/−), so colour is never the only signal.
- **Every state is written out** (Working, Pending cancel, Unconfirmed…), colour only repeats it.
- **No popups for routine things.** Problems go to the ladder's status line and the Messages
  widget. Only STOP and resume open a small dialog.
- **Nothing is sent blind.** While disconnected or stopped, order clicks do nothing and say why.

## 2. Design tokens

| Token | Value | Use |
|---|---|---|
| menu | `#12161B` | menu bar, workspace tabs |
| desktop | `#14181D` | space between windows |
| toolbar | `#181D24` | toolbar |
| body | `#1F262F` | window body |
| zebra | `#232B35` | alternate grid rows |
| chrome | `#262E39` | title bars, headers, buttons |
| border | `#39424F` | borders |
| text / text-2 | `#DDE3EA` / `#8D99A8` | text, secondary text |
| bid / bid-empty | `#2068C9` / `#1A3A66` | bid depth, empty bid cells |
| ask / ask-empty | `#C8323C` / `#5A1E24` | ask depth, empty ask cells |
| price | `#C4C9D0` (text `#14181D`) | ladder price column |
| last | `#1E9E5A` | last traded price, up-tick LTQ |
| work-buy / work-sell | `#2F7BFF` / `#E0464F` | my working orders |
| vap-bar | `#34506F` | volume-at-price bar |
| primary | `#0E9F7E` | Explore, Sign in, "All" |
| good / bad / pending | `#4CC38A` / `#F0646C` / `#E8B931` | text states |
| buy-text / sell-text | `#6FA8FF` / `#FF7B83` | side text in grids |
| stop | `#4A1B20` + border `#E0464F` | STOP, CXL ALL, stopped band `#6E1C22` |
| live strip / paper strip | `#1B4D3A` / `#6B5A12` | environment strip |

Sizes: title bar 24 px plus a 3 px link strip; toolbar controls 26 px; grid rows 22 px; ladder
rows 20 px (mini ladder 18 px); 4 px between windows. Font: Segoe UI on Windows, IBM Plex
Sans elsewhere.

## 3. The shell

```
menu bar      MockTrade · File Edit View Window Help                       user · account
toolbar       Widgets ▾ · Search product (/) · Explore ▾ ······ Prices● Kite● Recon● 12/350·min
              | Day +₹14,812.50 · Layouts ▾ · 10:14:52 · Save · XLS · Lock · STOP
env strip     LIVE · KITE · orders go to the exchange      (amber PAPER strip for paper)
band          (only when needed) TRADING STOPPED / AUTO-STOP / PRICES OLD / DISCONNECTED …
desktop       dockview: widgets docked, tabbed, split or floating
tabs          Main | Options | Spreads | Algos | +            loss limit used · working count
```

- **Widgets** menu adds any widget. **Search product** (`/`) finds an instrument and opens a
  ladder linked to the focused group.
- **Link groups:** pink, amber, blue or none. Widgets with the same colour follow each other's
  instrument (click in a watch list, the linked ladder and ticket switch).
- **Layouts** are saved per user on the server (and in browser storage as a fallback); a
  layout restores every widget with its props (instrument, link group, filters).
- **Status lights:** Prices (green live, amber > 5 s old, red down), Kite (login valid until
  06:00), Recon (positions match the broker). The rate meter shows orders this minute.
- **Bands** under the toolbar, most serious first: stopped (red), auto-stop (red), firm stop
  (dashed red), prices old (amber), disconnected (amber), Kite login needed (amber), audit log
  not writing (grey).

## 4. Widgets

### Ladder (the order entry)
Columns **Work | PIQ | Bids | Price | Asks | LTQ | VAP**; rows are prices on the tick grid.
- Header: link strip, title bar (contract, `? – ▢ ×`), net change (22 px), H/L/O/V, position and P&L.
- Sidebar: account, type (LMT/MKT), TIF (DAY/IOC), product (NRML/MIS), lots box with "= qty",
  `+1 +2 +5 +10 +25 +50` (add), `0` (clear), ▲ / AUTO / ▼ (scroll, auto-centre), CXL ALL.
- **Click Bids** = buy the lots box at that price; **click Asks** = sell. MKT uses market
  protection. The request goes through the same risk checks; a refusal shows in the status line.
- **Click Work** = select (yellow ring); again = cancel; another row's Work = move (modify the
  price, which goes to the back of the queue).
- Work shows my working quantity per price (blue buy, red sell; italic while a cancel is pending).
  PIQ shows the quantity ahead of my order, **estimated in the browser** with the paper broker's
  own rule (join behind the depth shown at my price when I click; trades at my price eat the
  queue; the queue is never longer than the depth shown minus me; a moved order goes to the
  back). Kite doesn't report queue position, so paper and live use the same estimate.
- Depth is Kite's 5 levels, best level bold, empty cells (never fake zeros) elsewhere. Price
  column grey, last price green. LTQ shows the last trade size (green up-tick, red down-tick).
  VAP is today's volume at each price with a bar: the server counts it from every tick
  (`GET /api/instruments/{id}/vap`, `marketdata/vap.py`) and the ladder continues the count from
  the stream, so it covers the whole day, not just since the ladder opened. It is an estimate:
  Kite sends snapshots, so volume between two snapshots is put on the newer last price.
- Status line: the last event for this ladder (sent, filled, refused with the reason…) and
  the working quantity. Refusals also go to Messages.
- **Confirm over** (sidebar "Cfm": Off, ₹1L, ₹5L, ₹25L): an order worth more needs a second
  click on the same cell within 5 s; the status line says what will be sent and its value.
- **CXL ALL** cancels this contract's working orders on the ladder's account. Cancels stay
  allowed while trading is stopped (getting out must always work); only disconnects and
  view-only accounts block them.
- **Unconfirmed** orders on a contract block new clicks on it until the broker answers.
- The browser makes each order's client id, so a lost answer to "place" can never turn into a
  second order; the status line says to check the order book.
- Keys (focused ladder): Space re-centre, Esc clear selection and lots, wheel over the prices
  scrolls them (and turns auto-centre off), wheel over the lots box ±1 lot,
  Ctrl+click Bids/Asks joins the best price on that side.

### Order ticket
Contract (follows its link group), BUY/SELL, lots (= qty), price and trigger with −/+ tick
buttons, the order types the broker offers, TIF, product, account, "confirm over". Under it: value,
worst-case position (position + working orders on that side + this order), distance from the
last price, and for market orders "pays at most" (the protected worst price). Problems are
found before the click and named in the status line (no price, off the tick grid, a stop
trigger the market already passed, a stop-loss trigger on the wrong side of its limit, a
validity or product the type doesn't allow). Over "confirm over", the button turns into
CONFIRM BUY 300 with Back focused; changing anything about the order cancels the confirmation.
Enter sends, Esc clears, ↑↓ move the focused price one tick.

### Backend provides, frontend displays: the order spec
The screens hard-code no order types, validities, products or market rules. For each account
and contract the server publishes an **order spec** (`GET /api/accounts/{a}/instruments/{id}/order-spec`,
`domain/order_spec.py`): each order type's code, label, name, help text, which fields it needs
(price / trigger: required or none; market protection: auto or none), its validities, its extra
rules (`stop_trigger`, `stop_limit`) and whether a ladder click can price it; plus products,
tick, lot and the automatic protection band. The ticket draws its type buttons, fields, TIF and
product lists from it; the ladder offers the click-priced types. A broker or exchange that adds
an order type changes only the backend (its adapter publishes the new spec), and the order
manager enforces the same spec, so the screen and the server can't disagree.

### Order book
Tabs Working / Filled / Rejected with counts; columns Time, Contract, Side, Qty, Filled, Price,
Status; Cancel per row and Cancel all. Statuses: Pending new, Working, Partial, Pending cancel,
Unconfirmed, Filled, Cancelled, Rejected (with reason).

### Positions
Contract, product, net, average, LTP, realised, unrealised, P&L; total row; the reconcile state
("✓ = Kite 2m" or a red "differs" line with ours vs broker).

### Audit trail
Filter bar (account, contract, exec type, from/to, live), columns Transact time, Account,
Contract, Order, User, Exec type, Side, Qty, Filled, Price, Detail; a selected order opens a
side panel with its timeline. Append-only; export CSV.

### Messages
Every refusal, warning and notice: time, level (ERROR/WARN/INFO/OK), contract, the full
sentence. Ladders show the same events in their status line.

### Watch
Instrument, LTP, change %; click opens or links a ladder.

### Algo manager (K6)
Toolbar: pause, resume, cancel, Backoff, Payup, All ▾, OCO; grid: instrument, algo, side,
price, qty, filled, state.

## 5. Safety interactions

- **STOP** (toolbar or Ctrl+Shift+K) opens a small dialog: what will be cancelled, what is
  blocked, what is kept (positions), and a reason. Enter = STOP NOW. The switch engages first,
  then cancels.
- While stopped: a red band on every screen, ladders show prices but dim the trading columns.
- **Unconfirmed** orders block sending the same order again until the broker's order book answers.
- **Disconnected** from the server: order clicks are paused; the page reconnects, then loads
  snapshots and merges by `updated_at` (see `stream.md`).

## 6. Phone
Watch, cancel, close and stop only; no new trades from a small screen. STOP sits at the bottom
where the thumb rests.

## 7. Build notes
- React + TypeScript (Vite), dockview for the workspace, one shared WebSocket client
  (`stream.md`) with reference-counted subscriptions: two widgets on one instrument share one
  subscription.
- Prices update a ladder at most every 250 ms (the server conflates); order events are applied
  one by one.
- Widgets get everything as props, so any widget **pops out** into its own window unchanged
  (⧉ on its tab; ⇲ or closing the window docks it back). The window loads `/popout.html` and
  is drawn by the main page, so it shares the one WebSocket, store and link groups: no second
  login, no sync delay. Pop-outs are saved in the layout. Works in the browser (pop-ups allowed)
  and in the desktop app (`ui/desktop/`, Electron), where they are real app windows on any
  monitor; each costs ~20–25 MB there (measured, see `ui/desktop/README.md`).
