# Reading order: every code file, in the order to read it

[Guide home](README.md). Read top to bottom. Each file only needs the files above it. Next to each file is its own first line (what it is for). Open the file, read its top comment, then its main class or function. Its test is `tests/test_<name>.py` (server) or `<name>.test.tsx` beside it (screens): read the test names, since they say what the file promises.

Tip: tick each file off as you read it. A file that feels hard: read its test first.

## The map (read the stages in this order)

```
 STAGE 1  the words         domain/            money, instruments, orders, ticks
    │                                          ("what things ARE", no logic yet)
    ▼
 STAGE 2  the lists         refdata/, marketdata/   which instruments exist; prices flowing
    ▼
 STAGE 3  the broker plug   broker/base, fake, rules, paper   where orders go (pretend first)
    ▼
 STAGE 4  THE HEART         orders/            the order manager: send → answer → fill
    ▼
 STAGE 5  the accountant    positions/         what you hold, profit and loss
    ▼
 STAGE 6  the guards        risk/              every check, STOP, loss limits, safe closing
    ▼
 STAGE 7  memory            store/             the ledger (events.db)
    ▼
 STAGE 8  wiring            platform.py, recovery.py   everything joined; surviving restarts
    ▼
 STAGE 9  the door          server/            login, the API, the live stream
    ▼
 STAGE 10 helpers           algos/, ops/       robots that trade; warnings and timers
    ▼
 STAGE 11 extras            charges, charts, alerts, options, portfolio, reports, research
    ▼
 STAGE 12 the real world    broker/kite/, tools/   Zerodha itself; the programs you run
    ▼
 SCREENS  ui/src/           api → state → shell → workspace → widgets (orders first)
```

**How to read one file (10 minutes each):**
1. Read the line next to it in this list.
2. Open the file and read the top comment: often there's a little drawing.
3. Find the main class or function. Read only its names first, then its body.
4. Open its test and read only the test names. They are sentences saying what it promises.
5. Say in one sentence what the file does, then tick it.

**If you're short on time, read just these 12 first** (they explain 80% of the system):
`domain/money.py` → `domain/orders.py` → `broker/base.py` → `broker/paper.py` →
`orders/manager.py` → `positions/book.py` → `risk/checks.py` → `risk/kill_switch.py` →
`store/events.py` → `platform.py` → `server/app.py` (search for `def place`) →
`ui/src/state/store.ts`

## Part A · The server (Python), 115 files


**domain/**

1. [ ] `src/mocktrade/domain/money.py` — Money and prices as whole paise.
2. [ ] `src/mocktrade/domain/instruments.py` — Instruments: what can be traded, identified by OUR ids, not any broker's.
3. [ ] `src/mocktrade/domain/orders.py` — Orders: what a user asks for, and the updates that describe its life.
4. [ ] `src/mocktrade/domain/ticks.py` — Ticks: one snapshot of an instrument's market, in our own ids and whole paise.
5. [ ] `src/mocktrade/domain/order_spec.py` — What can be ordered on an instrument: the order spec the screens are built from.
6. [ ] `src/mocktrade/domain/accounts.py` — Users and broker accounts: who may log in, and whose money an order uses.
7. [ ] `src/mocktrade/domain/gtt.py` — GTT (good till triggered): an order the BROKER holds until a price is touched.
8. [ ] `src/mocktrade/domain/portfolio.py` — An account's money and holdings as its broker reports them: holdings, funds, trades.

**refdata/**

9. [ ] `src/mocktrade/refdata/store.py` — The instrument master: every instrument by our id, plus each broker's id for it.

**marketdata/**

10. [ ] `src/mocktrade/marketdata/service.py` — The market data cache and its subscriptions.
11. [ ] `src/mocktrade/marketdata/sim_universe.py` — The contracts the market simulator prices, with their starting prices (paise).
12. [ ] `src/mocktrade/marketdata/simulator.py` — A market simulator: realistic ticks without Kite, for the demo server and UI work.
13. [ ] `src/mocktrade/marketdata/ladder.py` — Render a tick's 5-level depth as a text ladder (used by the terminal watcher and tests).
14. [ ] `src/mocktrade/marketdata/movers.py` — Movers: the biggest gainers, losers, volume and open-interest change among the instruments
15. [ ] `src/mocktrade/marketdata/prints.py` — Time & sales: the trades between ticks, taken from the tick stream (a MarketData tap).
16. [ ] `src/mocktrade/marketdata/vap.py` — Volume at price (VAP): how much traded today at each price, for the ladder's VAP column.

**broker/**

17. [ ] `src/mocktrade/broker/base.py` — The broker interface. Kite, Paper, a test Fake and future brokers all follow it.
18. [ ] `src/mocktrade/broker/fake.py` — A scripted broker for tests: records what it was asked and replays what a test tells it.
19. [ ] `src/mocktrade/broker/rules.py` — Rules every broker adapter applies the same way, so paper trading can't be more lenient
20. [ ] `src/mocktrade/broker/paper.py` — The paper broker: fake money, real prices.
21. [ ] `src/mocktrade/broker/paper_funds.py` — Paper money: the cash and margin a paper account has, worked out the way a broker would.

**orders/**

22. [ ] `src/mocktrade/orders/model.py` — What the order manager tells the rest of the app: order views, fills and events.
23. [ ] `src/mocktrade/orders/codec.py` — Order views to and from plain JSON-ready dicts, for the event log and the API.
24. [ ] `src/mocktrade/orders/gate.py` — The gate: what every order, modify and cancel must pass before it reaches the broker.
25. [ ] `src/mocktrade/orders/manager.py` — The order manager: one place that knows every order of one broker account.
26. [ ] `src/mocktrade/orders/basket.py` — Baskets: several orders sent together, after one check of them all.
27. [ ] `src/mocktrade/orders/gtt.py` — GTTs for one account: checked, placed at the broker, watched, and adopted when they fire.

**positions/**

28. [ ] `src/mocktrade/positions/book.py` — The position book: net positions and P&L for one broker account, from fills.
29. [ ] `src/mocktrade/positions/reconcile.py` — Reconcile our positions with the broker's: any difference must be seen at once.

**risk/**

30. [ ] `src/mocktrade/risk/limits.py` — Risk limits for one broker account: the numbers the pre-trade checks enforce.
31. [ ] `src/mocktrade/risk/limits_spec.py` — The risk limits as a spec: what an admin can set on an account, for the admin screen.
32. [ ] `src/mocktrade/risk/checks.py` — Pre-trade risk checks: the first line of defence before an order can leave.
33. [ ] `src/mocktrade/risk/throttle.py` — The throttle: never send order messages faster than the broker and SEBI allow.
34. [ ] `src/mocktrade/risk/margin.py` — The margin gate: refuse an order the account's funds can't carry, before it is sent.
35. [ ] `src/mocktrade/risk/kill_switch.py` — The kill switch: stop everything now.
36. [ ] `src/mocktrade/risk/loss_watch.py` — Auto kill switch on the daily loss: when today's P&L falls to the limit, stop trading.
37. [ ] `src/mocktrade/risk/closing.py` — Closing a position through the normal order path (risk, margin, throttle, audit).
38. [ ] `src/mocktrade/risk/flatten.py` — STOP, cancel and flatten: three separate safe operations (#262).
39. [ ] `src/mocktrade/risk/mtm.py` — MTM watch: loss limits per position and for the whole account, checked on every price.
40. [ ] `src/mocktrade/risk/squareoff.py` — The square-off assistant: intraday (MIS) positions before the broker closes them itself.

**store/**

41. [ ] `src/mocktrade/store/events.py` — The event log: every order event, append-only, in a crash-safe SQLite file.

**(top level)/**

42. [ ] `src/mocktrade/platform.py` — Putting it together: one AccountRuntime per broker account, inside one Platform.
43. [ ] `src/mocktrade/recovery.py` — After a restart: rebuild an account's trading state before it may trade again.

**server/**

44. [ ] `src/mocktrade/server/security.py` — Security building blocks: passwords, 2FA codes, session tokens, secrets at rest.
45. [ ] `src/mocktrade/server/users.py` — Users, broker accounts and login sessions, in a small SQLite file.
46. [ ] `src/mocktrade/server/formats.py` — How positions and algos look in JSON: shared by the API and the stream so they never differ.
47. [ ] `src/mocktrade/server/stream.py` — The live stream to the browser: one WebSocket per tab.
48. [ ] `src/mocktrade/server/app.py` — The HTTP API: login, accounts, orders, positions, kill switches, audit history.
49. [ ] `src/mocktrade/server/admin.py` — The admin API (K13.1): users, roles, broker accounts and each account's risk limits, without

**algos/**

50. [ ] `src/mocktrade/algos/spec.py` — What an algo needs from the trader: the algo spec the screens are built from.
51. [ ] `src/mocktrade/algos/engine.py` — The algo engine: parent orders worked by strategies through child orders.
52. [ ] `src/mocktrade/algos/builtin.py` — The algos MockTrade ships with. Add one here (spec + strategy) and every screen shows it.
53. [ ] `src/mocktrade/algos/execution.py` — What execution algos share: where to price a child, and keeping children at that price.
54. [ ] `src/mocktrade/algos/slicer.py` — The slicer: a big order sent as small limit orders over time (time-sliced execution).
55. [ ] `src/mocktrade/algos/iceberg.py` — The iceberg: a big order shown a little at a time, at the trader's limit price.
56. [ ] `src/mocktrade/algos/pov.py` — POV (percentage of volume): trade a set share of what the market trades, until done.
57. [ ] `src/mocktrade/algos/protect.py` — Protection held by MockTrade: trailing stop, OCO exit and bracket.
58. [ ] `src/mocktrade/algos/schedule.py` — TWAP and VWAP: an order worked to a schedule over a time window.
59. [ ] `src/mocktrade/algos/spreader.py` — The spreader: trade a two-leg spread (e.g. buy this month, sell next month) as one order.

**ops/**

60. [ ] `src/mocktrade/ops/alerts.py` — Operational alerts: one place, each told once, to every place that should hear it.
61. [ ] `src/mocktrade/ops/scheduler.py` — Run jobs at India times on trading days.
62. [ ] `src/mocktrade/ops/checks.py` — The checks the scheduler runs, each raising or clearing one alert.
63. [ ] `src/mocktrade/ops/guards.py` — On every screen (the alert band): MTM limits reached, and MIS positions before the cut-off.
64. [ ] `src/mocktrade/ops/notify.py` — Send alerts off the machine: a JSON POST to a webhook (Slack, Google Chat, ntfy, or any
65. [ ] `src/mocktrade/ops/protection.py` — On every screen (the alert band): positions that had protection and have lost it (V1.2, #328).

**charges/**

66. [ ] `src/mocktrade/charges/model.py` — What a trade costs beyond the price: brokerage and the taxes and fees on Indian trades.
67. [ ] `src/mocktrade/charges/schedules.py` — Charge schedules: rates as data, with the date they were checked.
68. [ ] `src/mocktrade/charges/service.py` — One account's charges for the day, and estimates for orders not yet sent.

**charts/**

69. [ ] `src/mocktrade/charts/candles.py` — Candles (OHLCV): built live from ticks, one level only (1 minute), added up for the rest.
70. [ ] `src/mocktrade/charts/history.py` — Past candles: where a chart's history comes from (replaceable).
71. [ ] `src/mocktrade/charts/service.py` — Charts: candles for a screen (history joined to live) and the account's trades as marks.

**alerts/**

72. [ ] `src/mocktrade/alerts/engine.py` — Market alerts: price, % move, day volume, open interest and spread, checked on every tick.
73. [ ] `src/mocktrade/alerts/service.py` — Market alerts for the server: the engine, kept per user, told to the screens, the audit
74. [ ] `src/mocktrade/alerts/store.py` — Alerts kept across restarts: one JSON file per user (their alerts and their webhook).
75. [ ] `src/mocktrade/alerts/webhook.py` — Alerts sent to a user's webhook: signed, retried, never on the trading loop's time.

**options/**

76. [ ] `src/mocktrade/options/chain.py` — The option chain: every strike of one underlying and expiry, priced and with Greeks.
77. [ ] `src/mocktrade/options/model.py` — From instruments and live prices to the numbers pricing needs.
78. [ ] `src/mocktrade/options/pricing.py` — Black-76: European options priced off the future, with implied volatility and Greeks.
79. [ ] `src/mocktrade/options/strategy.py` — Option strategies: templates, legs at live prices, and what the position does.

**portfolio/**

80. [ ] `src/mocktrade/portfolio/service.py` — What an account holds and owns, kept fresh from its broker: funds, holdings, trades.

**reports/**

81. [ ] `src/mocktrade/reports/daily.py` — The end-of-day report for one account, built from the audit trail (the source of truth).
82. [ ] `src/mocktrade/reports/export.py` — A day's report as a download: CSV (for a spreadsheet) or PDF (to read and keep).
83. [ ] `src/mocktrade/reports/history.py` — P&L over many days, from kept end-of-day reports: by day, by instrument, by strategy.
84. [ ] `src/mocktrade/reports/pdf.py` — A small PDF writer for text reports: monospaced lines on A4 pages, no dependency.
85. [ ] `src/mocktrade/reports/service.py` — Reports for the screens: a day's report (kept after the close, or a live preview), P&L history,
86. [ ] `src/mocktrade/reports/store.py` — Kept reports: one JSON file per account and day, written after the close.

**research/**

87. [ ] `src/mocktrade/research/backtest.py` — Backtests: an algo over recorded days, with the same engine, paper broker and risk checks as
88. [ ] `src/mocktrade/research/replay.py` — Replay a recorded day (K11.1) into MarketData, so paper trading runs on a real past day.
89. [ ] `src/mocktrade/research/ticks.py` — Recorded ticks: every tick MockTrade receives, kept for replay (K11.2) and backtests (K11.3).

**broker/kite/**

90. [ ] `src/mocktrade/broker/kite/broker.py` — KiteBroker: real orders through Kite Connect, behind the same Broker interface.
91. [ ] `src/mocktrade/broker/kite/daily_login.py` — Kite's daily login, started from the web page instead of the terminal.
92. [ ] `src/mocktrade/broker/kite/decoder.py` — Decode Kite's binary WebSocket frames into ticks keyed by Kite's instrument token.
93. [ ] `src/mocktrade/broker/kite/encode.py` — Build Kite binary frames (big-endian, paise), exactly as Kite sends them.
94. [ ] `src/mocktrade/broker/kite/feed.py` — Glue between Kite's ticker and MockTrade: Kite tokens in, our ticks out.
95. [ ] `src/mocktrade/broker/kite/gtt.py` — Kite's GTT API (/gtt/triggers) in MockTrade's terms.
96. [ ] `src/mocktrade/broker/kite/http.py` — A small async client for Kite Connect's REST API (v3).
97. [ ] `src/mocktrade/broker/kite/instruments.py` — Load Kite's daily instrument dump (CSV) into the instrument master.
98. [ ] `src/mocktrade/broker/kite/live.py` — Live Kite inside the server: each kite account's connection, from start-up to 6 AM.
99. [ ] `src/mocktrade/broker/kite/orders.py` — Translating orders between MockTrade and Kite (pure functions, no network).
100. [ ] `src/mocktrade/broker/kite/portfolio.py` — Kite's holdings, margins and tradebook in MockTrade's terms.
101. [ ] `src/mocktrade/broker/kite/session.py` — Kite login: turn the account holder's manual login into an access token.
102. [ ] `src/mocktrade/broker/kite/ticker.py` — Kite's live WebSocket: prices (binary) and order updates (JSON text), kept alive forever.
103. [ ] `src/mocktrade/broker/kite/token_store.py` — Keep today's Kite access token in a private local file between commands.

**tools/**

104. [ ] `src/mocktrade/tools/demo.py` — Run the whole platform locally with simulated prices and paper trading: no Kite needed.
105. [ ] `src/mocktrade/tools/serve.py` — Run MockTrade as a server: the entry point the Docker image starts.
106. [ ] `src/mocktrade/tools/amo_check.py` — Go-live check: place ONE after-market order (AMO) through Kite, then cancel it.
107. [ ] `src/mocktrade/tools/backup.py` — Back up everything a restored MockTrade server needs, safely while the server runs (V1.3).
108. [ ] `src/mocktrade/tools/loadtest.py` — Load test: the real server in its own process, fed by a fake Kite, read by screen-like clients.
109. [ ] `src/mocktrade/tools/login.py` — Log in to Kite from the terminal: python -m mocktrade.tools.login
110. [ ] `src/mocktrade/tools/restore.py` — Restore a backup into a data folder (V1.3). With the server stopped:
111. [ ] `src/mocktrade/tools/users.py` — Manage MockTrade users and accounts from the server's terminal.
112. [ ] `src/mocktrade/tools/watch.py` — Watch a live ladder in the terminal: python -m mocktrade.tools.watch NFO:NIFTY26OCTFUT

**(top level)/**

113. [ ] `src/mocktrade/config.py` — Settings read from the environment (and an optional .env file for local use).
114. [ ] `src/mocktrade/metrics.py` — Measurements: how long each step takes, as histograms, so speed is a number, not a guess.
115. [ ] `src/mocktrade/version.py` — MockTrade's version (V1.6, #289): one place, shown by /api/health and on the screens.

## Part B · The screens (TypeScript/React), 85 files


**api/**

1. [ ] `ui/src/api/rest.ts` — Talking to the server's REST API. The session cookie travels automatically; changing
2. [ ] `ui/src/api/stream.ts` — The one WebSocket per tab (docs/design/stream.md).
3. [ ] `ui/src/api/types.ts` — The shapes the server sends (docs/design/stream.md and the REST API). Money in paise.

**app/**

4. [ ] `ui/src/app/context.tsx` — The services every widget shares, and the hooks widgets use to read them.

**state/**

5. [ ] `ui/src/state/gate.ts` — "Nothing is sent blind" (docs/design/ui.md §1): before any order click, why it can't go.
6. [ ] `ui/src/state/instruments.ts` — Instrument details (tick size, lot size, name) fetched once and shared by every widget.
7. [ ] `ui/src/state/orderSpecs.ts` — Order specs (what can be ordered on an instrument through an account) fetched once per
8. [ ] `ui/src/state/session.ts` — The session: logging in and out, and keeping the Store in step with the server.
9. [ ] `ui/src/state/store.ts` — Everything the screens show that isn't a price: who is logged in, orders, positions,

**lib/**

10. [ ] `ui/src/lib/format.ts` — How numbers, money and times are written on screen. Money arrives in paise (whole numbers),

**theme/**

11. [ ] `ui/src/theme/theme.ts` — The screen's theme (K13.5): dark, light or colour-blind safe, or "system" (follow the computer's

**components/**

12. [ ] `ui/src/components/Menu.tsx` — A small drop-down: a toolbar button that opens a list. Closes on outside click or Esc.
13. [ ] `ui/src/components/QrCode.tsx` — A QR code drawn as an SVG, for the authenticator link an admin hands to a new user (scan it
14. [ ] `ui/src/components/SearchBox.tsx` — Find an instrument by symbol: type, arrow through the matches, Enter to pick.

**shell/**

15. [ ] `ui/src/shell/Bands.tsx` — The bands under the toolbar: only when something needs attention, most serious first
16. [ ] `ui/src/shell/EnvStrip.tsx` — The strip under the toolbar: which world orders go to (paper, live, none), and for a
17. [ ] `ui/src/shell/Login.tsx` — Sign in: username, password and the 6-digit authenticator code. The server never says
18. [ ] `ui/src/shell/ReplayBar.tsx` — The replay bar: shown only on a replay server (K11.2), where prices come from a recorded day.
19. [ ] `ui/src/shell/RestoreStop.tsx` — "Restore stop" in an UNPROTECTED band (#264): starts the stop the position had again, sized
20. [ ] `ui/src/shell/ServerVersion.tsx` — The running server's version in the status bar (V1.6, #289), as the server says it
21. [ ] `ui/src/shell/SetPassword.tsx` — "Set your password": shown instead of the terminal when an admin made this user, or reset their
22. [ ] `ui/src/shell/Shell.tsx` — The terminal around the widgets:
23. [ ] `ui/src/shell/StopBand.tsx` — The STOP band (#262): what the STOP did, in the server's own words, and Flatten.
24. [ ] `ui/src/shell/StopDialog.tsx` — STOP and resume: the only dialogs in the terminal (docs/design/ui.md §5).
25. [ ] `ui/src/shell/ThemePick.tsx` — The theme picker in the top bar (K13.5): the new colours show at once and are saved for the

**workspace/**

26. [ ] `ui/src/workspace/Panel.tsx` — The frame around every widget inside dockview: hands the widget its saved params, saves
27. [ ] `ui/src/workspace/Workspace.tsx` — The desktop: dockview with every widget kind registered, plus the page tabs along the
28. [ ] `ui/src/workspace/controller.ts` — The workspace behind the screen: its pages (Main, Options, …), which one is showing, and
29. [ ] `ui/src/workspace/defaults.ts` — The workspace a new user starts with: a Watch list on the left, ladders for the first
30. [ ] `ui/src/workspace/layouts.ts` — Saving and restoring the workspace: which widgets are open, where, and with what settings.
31. [ ] `ui/src/workspace/links.ts` — Link groups: widgets with the same colour follow the same instrument. Click a row in a
32. [ ] `ui/src/workspace/registry.ts` — Every kind of widget the workspace can open. A widget is a React component that gets

**keys/**

33. [ ] `ui/src/keys/bindings.ts` — The user's keyboard shortcuts, kept on the server (GET/PUT /api/me/settings/keys) so they
34. [ ] `ui/src/keys/model.ts` — Keyboard shortcuts (K13.4): one list of every action a key can start, each with its default

**widgets/books/**

35. [ ] `ui/src/widgets/books/AuditTrail.tsx` — Audit trail: everything that happened on the account, newest first, append-only (canvas
36. [ ] `ui/src/widgets/books/Messages.tsx` — Messages: every refusal, warning and notice, newest first (docs/design/ui.md §4). Ladders
37. [ ] `ui/src/widgets/books/OrderBook.tsx` — Order book: every order on the account, live, in tabs (canvas board 2, docs/design/ui.md §4).
38. [ ] `ui/src/widgets/books/Positions.tsx` — Positions: net quantity and P&L per contract, live, with a total row and whether MockTrade's
39. [ ] `ui/src/widgets/books/SquareOff.tsx` — The square-off assistant in the Positions bar: open intraday (MIS) positions before the
40. [ ] `ui/src/widgets/books/model.ts` — Rules shared by the order book, positions and audit trail, kept apart from React so each

**widgets/ladder/**

41. [ ] `ui/src/widgets/ladder/Ladder.tsx` — The ladder: the trader's main order entry (docs/design/ui.md §4, canvas board 0).
42. [ ] `ui/src/widgets/ladder/model.ts` — The ladder's rules, kept apart from React so each one can be tested on its own.

**widgets/ticket/**

43. [ ] `ui/src/widgets/ticket/Ticket.tsx` — Order ticket: every order type from a form (docs/design/ui.md §4, canvas board 3).
44. [ ] `ui/src/widgets/ticket/model.ts` — The order ticket's rules (docs/design/ui.md §4 "Order ticket"), apart from React so each
45. [ ] `ui/src/widgets/ticket/useChargeEstimate.ts` — What an order would pay in charges, and the margin it would block, if it filled in full:

**widgets/**

46. [ ] `ui/src/widgets/Watch.tsx` — Watch list: instruments with live last price and change. Click a row to send it to the

**widgets/admin/**

47. [ ] `ui/src/widgets/admin/Admin.tsx` — Admin: users, broker accounts and each account's risk limits, without the server's terminal
48. [ ] `ui/src/widgets/admin/model.ts` — Admin screen logic (K13.2): the limits form from the server's limits spec, what a user's or an
49. [ ] `ui/src/widgets/admin/testdata.ts` — The limits spec and limits as the server sends them (a part of the real spec, from

**widgets/alerts/**

50. [ ] `ui/src/widgets/alerts/AlertsManager.tsx` — Alerts: your price, % move, volume, OI and spread alerts (K12.4; the engine is K12.3,
51. [ ] `ui/src/widgets/alerts/model.ts` — Alerts manager logic (K12.4): the create request from the server's alert spec, what an
52. [ ] `ui/src/widgets/alerts/testdata.ts` — Alerts test data, shared by the model and widget tests.

**widgets/algos/**

53. [ ] `ui/src/widgets/algos/AlgoManager.tsx` — Algo manager: start, watch and control algos (docs/design/algos.md, K6.4).
54. [ ] `ui/src/widgets/algos/model.ts` — Algo manager logic, kept out of the widget so it can be tested on its own.

**widgets/chain/**

55. [ ] `ui/src/widgets/chain/OptionChain.tsx` — Option chain: calls on the left, strikes in the middle, puts on the right, live (K8.3).
56. [ ] `ui/src/widgets/chain/model.ts` — Option chain logic, kept out of the widget so it can be tested on its own. The server sends

**widgets/chart/**

57. [ ] `ui/src/widgets/chart/Chart.tsx` — Chart: candles with a volume pane, the live forming candle, the account's fills as marks,
58. [ ] `ui/src/widgets/chart/draw.ts` — Drawing a chart on a canvas: candles (up hollow, down filled: shape as well as colour, so
59. [ ] `ui/src/widgets/chart/model.ts` — Chart logic, kept out of the widget so it can be tested without a canvas: merging candles
60. [ ] `ui/src/widgets/chart/testdata.ts` — Chart test data, shared by the model, drawing and widget tests.

**widgets/gtt/**

61. [ ] `ui/src/widgets/gtt/GttBook.tsx` — GTT book: orders the BROKER holds until a price is touched (docs/design/gtt.md, K9.3/K9.4).
62. [ ] `ui/src/widgets/gtt/model.ts` — GTT form rules (docs/design/gtt.md), apart from React so each can be tested on its own.

**widgets/**

63. [ ] `ui/src/widgets/index.ts` — Importing a widget registers it with the workspace (Widgets ▾ and saved layouts).

**widgets/keys/**

64. [ ] `ui/src/widgets/keys/KeysEditor.tsx` — Keyboard shortcuts: every action a key can start, by where it works, with its keys (K13.4;

**widgets/market/**

65. [ ] `ui/src/widgets/market/Depth.tsx` — Market depth: the book's levels for one contract, with bars, running totals, the whole
66. [ ] `ui/src/widgets/market/Movers.tsx` — Movers: the biggest gainers, losers, volume and open-interest change among the contracts the
67. [ ] `ui/src/widgets/market/TimeAndSales.tsx` — Time & sales: the trades between price snapshots for one contract, newest on top, with the
68. [ ] `ui/src/widgets/market/model.ts` — Market screens logic (K12.4): the depth window's numbers and the time & sales tape. Kept out of

**widgets/mtm/**

69. [ ] `ui/src/widgets/mtm/MtmLimits.tsx` — MTM Limits: the trader's own loss limits, per position and for the whole account, watched

**widgets/portfolio/**

70. [ ] `ui/src/widgets/portfolio/FundsPanel.tsx` — Funds: what the account can still use, what is blocked, and why, per segment, as the broker
71. [ ] `ui/src/widgets/portfolio/HoldingsPanel.tsx` — Holdings: delivery shares kept from earlier days, at live prices, with P&L and the day's
72. [ ] `ui/src/widgets/portfolio/TradesTable.tsx` — The tradebook (the order book's Trades tab): every fill at the exchange today, newest first,
73. [ ] `ui/src/widgets/portfolio/model.ts` — Funds, holdings and trades as screens show them. Every figure comes from the server
74. [ ] `ui/src/widgets/portfolio/usePortfolio.ts` — One kind of portfolio figures (funds, holdings or trades) for the selected account, with a

**widgets/reports/**

75. [ ] `ui/src/widgets/reports/Reports.tsx` — Reports: the end-of-day report of a day (kept after the close, or today's preview) and the
76. [ ] `ui/src/widgets/reports/model.ts` — Reports as screens show them: which days can be picked, where downloads come from, how a

**widgets/research/**

77. [ ] `ui/src/widgets/research/Backtests.tsx` — Backtests: start an algo over recorded days, follow the run, read its results, compare runs
78. [ ] `ui/src/widgets/research/LineChart.tsx` — A small SVG line chart for research curves (equity over days, P&L through a day).
79. [ ] `ui/src/widgets/research/Recordings.tsx` — Recordings: the days of ticks this server has kept (K11.1), for replays and backtests.
80. [ ] `ui/src/widgets/research/model.ts` — Research screens logic (K11.4), kept out of the widgets so it can be tested on its own:
81. [ ] `ui/src/widgets/research/testdata.ts` — Research test data, shared by the model and widget tests.

**widgets/strategy/**

82. [ ] `ui/src/widgets/strategy/StrategyBuilder.tsx` — Strategy builder: pick a template (or Alt-click legs in the option chain), edit the legs,
83. [ ] `ui/src/widgets/strategy/model.ts` — Strategy builder logic: the payoff chart's geometry and the numbers' words. The server does

**(top level)/**

84. [ ] `ui/src/main.tsx` — 
85. [ ] `ui/src/App.tsx` — The page: checks the session, then shows the sign-in, the "set your password" page (after an
