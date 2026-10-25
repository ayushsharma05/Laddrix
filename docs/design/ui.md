# Laddrix UI: the terminal

The screens are a **trading terminal**, not a website: many small windows (widgets) in one
workspace, dense grids, click-to-trade ladders, fixed-size text. Convenience for the trader
comes first: the common action is one click, the dangerous one is never one accidental click.

The visual reference is the "Laddrix UI design" canvas (8 boards: ladder, design system,
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
  **An open connection isn't enough (#268):** after each (re)connect, an account takes no new
  orders until its orders and positions have loaded for that connect.
  - The band says so: "LOADING acc", or "COULDN'T LOAD acc" with why. A failed load is tried
    again every 3 s while connected.
  - Cancels still go: the server decides.
  - Answers from an older connect are dropped (a load "epoch").
  - Algos or GTTs that couldn't load show as **UNAVAILABLE**, never as an empty list
    (`state/session.ts`, `state/gate.ts`).

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
menu bar      Laddrix · File Edit View Window Help                       user · account
toolbar       Widgets ▾ · Search product (/) · Explore ▾ ······ Prices● Kite● Recon● 12/350·min
              | Day +₹14,812.50 · Layouts ▾ · 10:14:52 · Save · XLS · Lock · STOP
env strip     LIVE · KITE · orders go to the exchange      (amber PAPER strip for paper)
band          (only when needed) TRADING STOPPED / AUTO-STOP / PRICES OLD / DISCONNECTED …
desktop       the page: free windows on a scrolling canvas, or docked tiles (dockview)
tabs          Main FREE | Options DOCKED | Spreads | +        loss limit used · working count
```

### Free and docked pages (#410)

Each page tab chooses how its widgets are arranged; the small FREE / DOCKED label on the
showing tab switches it, keeping its widgets.

| | **Free** (new pages, first-run Main) | **Docked** (pages saved before #410) |
| --- | --- | --- |
| place | anywhere, gaps allowed | tiles that always fill the page |
| size | any (at least 160 × 80 px) | split with the neighbours |
| past the screen | the page scrolls, down and sideways | never |
| tabs in one window | no: one widget per window | yes |

```
free page: a canvas bigger than the screen when windows sit past its edge
┌ screen ─────────────────────────────────┐
│ ┌Watch─┐ ┌Ladder┐            ┌Ticket┐   │
│ │      │ │      │  (empty)   │      │   │
│ └──────┘ └──────┘            └──────┘   │
└─────────────────────────────────────────┘ ← scroll
            ┌Order Book──────────┐
            └────────────────────┘          canvas = furthest window + 240 px of room
```

- **Move:** drag the title bar. **Resize:** drag any edge or corner. A click brings a window
  to the front. A double-click on the title bar fills the visible page; again to put it back.
- **Snap:** an edge within 8 px of a neighbour's edge sticks to it: lined up with it, or
  beside it with a 4 px gap. Otherwise the box lands on an 8 px grid. **Hold Alt** for no
  snapping: the window goes exactly where the mouse is.
- **Keys:** a focused title bar moves with the arrow keys (8 px, Alt: 1 px); Shift+arrows
  resize.
- **New windows** open beside the window in front (ladders and tickets to its right, grids
  below) if that spot is empty and on screen, else in the first empty spot on screen, else a
  step down and right of the screen's corner. The page scrolls to a new window it can't see.
- **Dragging near the page's edge** scrolls it, so a window can be carried anywhere.
- **Lock** stops moving, resizing, closing and switching the page's mode.
- **Pop-out** works as on docked pages; a free page's popped-out windows come back onto the
  page when it is reloaded or another page is shown (browsers block opening them unasked).
- **Switching:** docked → free keeps each tile where it was on screen (a tab behind another
  sits a step down and right); free → docked tiles the windows row by row, top to bottom.
- **Saved** in the same layout document: a free page is `{name, mode: "free", dock: null,
  canvas: {windows: [{id, kind, params, x, y, w, h, z}]}}`, at most 200 windows a page. A
  page without `mode` is docked, so every layout saved before #410 opens as it did.
- Code: `ui/src/workspace/canvas.ts` (the rules, pure functions), `FreeCanvas.tsx` (the
  windows), `controller.ts` (pages, saving).

### The right-click menu (#412)

The browser's own menu never shows in Laddrix (except in text boxes, which keep copy and
paste, and on Shift+right-click). A left click stays for trading: a click on a ladder price
sends an order, so menus are on the right button, the keyboard's Menu key and Shift+F10.

```
right-click on …                       menu
───────────────────────────            ─────────────────────────────────────────────────
empty space, toolbar, page tabs        Open widget ▸ (on a free page: opens where clicked)
                                       Search product… · This page ▸ (Free / Docked,
                                       Rename…, Close page) · New page ▸ (Free / Docked)
                                       Save layout now · Lock layout · Theme ▸
a widget (its body or title bar)       its own settings (ladder: Lots per click ▸,
                                       Confirm orders over ▸; ticket: Show ▸, Lots ▸, …)
                                       Open for this contract ▸ · Link group ▸
                                       Duplicate · Pop out (or Put back) · Close
```

- A widget's own items come from its registration (`menu` in `registerWidget`), so a new
  widget, such as the spread ladder, adds its own (e.g. "Autospreader settings…") without
  touching the menu code.
- Keys: ↑ ↓ move, → opens a submenu, ← goes back, Enter chooses, Esc closes.
- The menu works in pop-out windows too, and is never drawn off screen.
- Code: `ui/src/components/ContextMenu.tsx` (the menu), `ui/src/workspace/menus.ts` (what
  each menu offers), `useRightClickMenu` in `ui/src/shell/Shell.tsx` (which menu to show).

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

### Autospreader (#414)
Your spreads of 2 to 6 legs: each one's legs ("+1 NIFTY26NOVFUT −1 NIFTY26DECFUT") and its
live implied bid and ask with their size in spread lots. **New** / **Edit** define the legs:
contract (search), ratio, price ×, which leg quotes, pay-up. The server checks the rules and
says what's wrong in words. **Ladder** opens the spread's ladder: the normal ladder on its
implied prices, where a click buys or sells spread lots (the Autospreader algo works the legs)
and the right-click menu offers "Autospreader settings…". Design: [spreads.md](spreads.md).

## 5. Safety interactions

- **STOP** (toolbar or Ctrl+Shift+K) opens a small dialog: what will be cancelled, what is
  blocked, what is kept (positions), and a reason. Enter = STOP NOW. The switch engages first,
  then cancels.
- While stopped: a red band on every screen, ladders show prices but dim the trading columns.
  The band shows the server's report of what the STOP did and, when positions are open,
  **Flatten (N)** (two clicks), which closes them without resuming trading (#262).
- **Unconfirmed** orders block sending the same order again until the broker's order book answers.
- **Disconnected** from the server: order clicks are paused; the page reconnects, then loads
  snapshots and merges by `updated_at` (see `stream.md`).

## 6. Phone
Watch, cancel, close and stop only; no new trades from a small screen. STOP sits at the bottom
where the thumb rests.

## 7. Build notes
- React + TypeScript (Vite), dockview for docked pages and our own canvas for free pages, one shared WebSocket client
  (`stream.md`) with reference-counted subscriptions: two widgets on one instrument share one
  subscription.
- Prices update a ladder at most every 250 ms (the server conflates); order events are applied
  one by one.
- **The workspace is the user's** (Ayush, 16 Oct): any widget opens, closes and sizes anywhere on a page; pages are the screens, and widgets pop out onto other monitors. Together they make one workspace, saved per user. So **every widget must work at any size**: a widget adapts to its own width (CSS container queries), never to the window's, and scrolls rather than cutting anything off.
- Widgets get everything as props, so any widget **pops out** into its own window unchanged
  (⧉ on its tab; ⇲ or closing the window docks it back). The window loads `/popout.html` and
  is drawn by the main page, so it shares the one WebSocket, store and link groups: no second
  login, no sync delay. Pop-outs are saved in the layout. Works in the browser (pop-ups allowed)
  and in the desktop app (`ui/desktop/`, Electron), where they are real app windows on any
  monitor; each costs ~20–25 MB there (measured, see `ui/desktop/README.md`).

## Keyboard shortcuts (K13.4, #284)

- **One list of actions** (`ui/src/keys/model.ts`). Each action has an id, a label, where it works and its default keys:
  - **anywhere:** STOP, search, save the layout, open a ticket, ladder or chart;
  - **order ticket:** send, clear, buy or sell side, price ↑↓;
  - **ladder:** centre, clear;
  - **chart:** pan, zoom, back to the latest.

  Widgets ask "which action is this key?" for their own scope (`useKeyAction(scope)`) and never hard-code a key. The ticket's hint line shows the user's actual keys.
- **Key names** are the same on every machine: "Ctrl+Shift+K", "Alt+B", "Enter", "/". ⌘ on a Mac counts as Ctrl. With Alt or Ctrl the letter is read from its place on the keyboard (macOS turns Alt+B into "∫"). Shift is named only where it matters ("?" is just "?").
- **Rules:**
  - Two actions of one scope can't share a key.
  - A global key can't also be a widget key, because both would fire.
  - Different widgets may share one (Escape clears both the ticket and the ladder).
  - Keys the browser keeps for itself (closing, reloading, switching tabs, Tab between fields) are refused, said in words.
  - Plain keys don't fire while typing in a box ("/" in a search field is a slash); keys with Ctrl or Alt, and function keys, do.
- **STOP always keeps Ctrl+Shift+K**, whatever else it is given. It can't be removed, and no other action can take it.
- **The Keyboard shortcuts widget:**
  - "+ key", then press the key (Esc cancels);
  - × removes a key;
  - "Default" resets one action;
  - "All back to the defaults" takes a second click.
  - Sending an order by key still goes through the ticket's own confirm rules.
- **Saved per user on the server** (`GET`/`PUT /api/me/settings/keys`), so the keys follow the user to another machine.
  - It is a small per-user settings store (`users.db`, `user_settings`). Only the known names `keys` and `theme` are accepted, at most 16 KB each, and `null` forgets a setting.
  - Saved choices are cleaned when they load: an action that no longer exists, a browser key, or a clash falls back to the defaults, so no key is ever left dead.
- **Checks:**
  - `ui/src/keys/model.test.ts`, `widgets/keys`, the ticket's own-keys test, and `tests/test_server.py` (settings);
  - `context/scripts/mutate-keys.py` (27/27 caught);
  - `context/scripts/e2e-keys.cjs` on the demo, in a real browser. Ctrl+W was refused in words and the tab stayed open. F2 opened a chart. F9 opened the stop dialog, and Ctrl+Shift+K still did. The keys survived a reload. "Back to the defaults" took two clicks. No page errors.

## Themes (K13.5, #285)

- **Three themes:** dark (the default), light, and colour-blind safe. The colour-blind one is dark with blue for buy, bids and profit, orange for sell, asks and loss, and purple for the last traded price, so nothing depends on telling red from green.
- **Every colour is a named token** in `ui/src/styles/theme.css` (for example `--bid`, `--ladder-ink`, `--on-strong`). The other stylesheets only use tokens; a test fails if a colour is written anywhere else. A theme is one block, `:root[data-theme="light"] { … }`, that sets the tokens again. Canvases (the chart) read the tokens when they draw, and redraw when the theme changes.
- **Readable in every theme:** a test checks every text colour on the surface it sits on against WCAG AA (contrast at least 4.5:1). Making all three pass needed a few dark colours a little deeper (bid, ask, last, the buy and sell buttons). The colour-blind test also checks the hues: buy colours blue, sell colours orange, the last price neither.
- **Choosing:** the Theme menu in the top bar. Until the user chooses, the screen follows the computer's own light or dark setting, and changes with it. A choice shows at once (no reload) and is saved for the user on the server (`PUT /api/me/settings/theme`, the store from K13.4), so it follows them. "Same as the computer" saves nothing. If the server refuses, the old theme comes back and the reason is shown.
- **No flash on load:** this browser keeps a copy of the last choice, so the sign-in page already has the right colours before the server answers. Blocked browser storage changes nothing else.
- **Checks:**
  - `ui/src/styles/themes.test.ts` (contrast, hues, every token set, no stray colours), `ui/src/theme/theme.test.ts`, `ui/src/shell/ThemePick.test.tsx`;
  - `context/scripts/mutate-themes.py` (18/18 caught; it found two gaps, now fixed);
  - `context/scripts/e2e-themes.cjs` on the demo in a real browser: a computer set to light shows light; Dark, Colour-blind and Light each switch without a reload and repaint the chart; the choice survives a reload; no page errors. Screenshots: `context/images/k13-5-theme-*.png`.
