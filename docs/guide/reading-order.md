# Reading order: every code file, in the order to read it

[Guide home](README.md). Read top to bottom. Each file only needs the files above it. Next to each file is its own first sentence (what it is for), taken from the file itself by `context/scripts/render-reading-order.py`; a test fails if a code file is missing here. Open the file, read its top comment, then its main class or function. Its test is `tests/test_<name>.py` (server) or `<name>.test.tsx` beside it (screens): read the test names, since they say what the file promises.

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
`store/events.py` → `platform.py` → `server/routes/trading.py` (search for `def place`) →
`ui/src/state/store.ts`

## Part A · The server (Python), 144 files

**domain/**

1. [ ] `src/laddrix/domain/money.py` — Money and prices as whole paise.
2. [ ] `src/laddrix/domain/instruments.py` — Instruments: what can be traded, identified by OUR ids, not any broker's.
3. [ ] `src/laddrix/domain/orders.py` — Orders: what a user asks for, and the updates that describe its life.
4. [ ] `src/laddrix/domain/ticks.py` — Ticks: one snapshot of an instrument's market, in our own ids and whole paise.
5. [ ] `src/laddrix/domain/order_spec.py` — What can be ordered on an instrument: the order spec the screens are built from.
6. [ ] `src/laddrix/domain/accounts.py` — Users and broker accounts: who may log in, and whose money an order uses.
7. [ ] `src/laddrix/domain/gtt.py` — GTT (good till triggered): an order the BROKER holds until a price is touched.
8. [ ] `src/laddrix/domain/portfolio.py` — An account's money and holdings as its broker reports them: holdings, funds, trades.

**refdata/**

9. [ ] `src/laddrix/refdata/store.py` — The instrument master: every instrument by our id, plus each broker's id for it.
10. [ ] `src/laddrix/refdata/expiry.py` — Expiry day for futures and options (#290): when a contract ends, how it settles, and what may still be ordered on it.
11. [ ] `src/laddrix/refdata/freeze.py` — Freeze quantities (#424): the exchange's most per order, for each underlying's futures and options.

**marketdata/**

12. [ ] `src/laddrix/marketdata/service.py` — The market data cache and its subscriptions.
13. [ ] `src/laddrix/marketdata/sim_universe.py` — The contracts the market simulator prices, with their starting prices (paise).
14. [ ] `src/laddrix/marketdata/simulator.py` — A market simulator: realistic ticks without Kite, for the demo server and UI work.
15. [ ] `src/laddrix/marketdata/ladder.py` — Render a tick's 5-level depth as a text ladder (used by the terminal watcher and tests).
16. [ ] `src/laddrix/marketdata/movers.py` — Movers: the biggest gainers, losers, volume and open-interest change among the instruments this server has prices for.
17. [ ] `src/laddrix/marketdata/prints.py` — Time & sales: the trades between ticks, taken from the tick stream (a MarketData tap).
18. [ ] `src/laddrix/marketdata/vap.py` — Volume at price (VAP): how much traded today at each price, for the ladder's VAP column.
19. [ ] `src/laddrix/marketdata/markets.py` — What the screens read from one price source (#395): one per subscription.
20. [ ] `src/laddrix/marketdata/sources.py` — Where each account's prices come from (#395, docs/design/tenancy.md §4).

**broker/**

21. [ ] `src/laddrix/broker/base.py` — The broker interface.
22. [ ] `src/laddrix/broker/fake.py` — A scripted broker for tests: records what it was asked and replays what a test tells it.
23. [ ] `src/laddrix/broker/rules.py` — Rules every broker adapter applies the same way, so paper trading can't be more lenient than real trading.
24. [ ] `src/laddrix/broker/paper.py` — The paper broker: fake money, real prices.
25. [ ] `src/laddrix/broker/paper_funds.py` — Paper money: the cash and margin a paper account has, worked out the way a broker would.

**orders/**

26. [ ] `src/laddrix/orders/model.py` — What the order manager tells the rest of the app: order views, fills and events.
27. [ ] `src/laddrix/orders/codec.py` — Order views to and from plain JSON-ready dicts, for the event log and the API.
28. [ ] `src/laddrix/orders/gate.py` — The gate: what every order, modify and cancel must pass before it reaches the broker.
29. [ ] `src/laddrix/orders/manager.py` — The order manager: one place that knows every order of one broker account.
30. [ ] `src/laddrix/orders/basket.py` — Baskets: several orders sent together, after one check of them all.
31. [ ] `src/laddrix/orders/gtt.py` — GTTs for one account: checked, placed at the broker, watched, and adopted when they fire.
32. [ ] `src/laddrix/orders/trade_check.py` — Orders against the broker's own trade list (#352, part of #275).

**positions/**

33. [ ] `src/laddrix/positions/book.py` — The position book: net positions and P&L for one broker account, from fills.
34. [ ] `src/laddrix/positions/reconcile.py` — Reconcile our positions with the broker's: any difference must be seen at once.
35. [ ] `src/laddrix/positions/external.py` — Trades made outside Laddrix (#355, part of #276): resolving a position mismatch they cause.

**risk/**

36. [ ] `src/laddrix/risk/limits.py` — Risk limits for one broker account: the numbers the pre-trade checks enforce.
37. [ ] `src/laddrix/risk/limits_spec.py` — The risk limits as a spec: what an admin can set on an account, for the admin screen.
38. [ ] `src/laddrix/risk/checks.py` — Pre-trade risk checks: the first line of defence before an order can leave.
39. [ ] `src/laddrix/risk/throttle.py` — The throttle: never send order messages faster than the broker and SEBI allow.
40. [ ] `src/laddrix/risk/margin.py` — The margin gate: refuse an order the account's funds can't carry, before it is sent.
41. [ ] `src/laddrix/risk/kill_switch.py` — The kill switch: stop everything now.
42. [ ] `src/laddrix/risk/loss_watch.py` — Auto kill switch on the daily loss: when today's P&L falls to the limit, stop trading.
43. [ ] `src/laddrix/risk/closing.py` — Closing a position through the normal order path (risk, margin, throttle, audit).
44. [ ] `src/laddrix/risk/flatten.py` — STOP, cancel and flatten: three separate safe operations (#262).
45. [ ] `src/laddrix/risk/mtm.py` — MTM watch: loss limits per position and for the whole account, checked on every price.
46. [ ] `src/laddrix/risk/squareoff.py` — The square-off assistant: intraday (MIS) positions before the broker closes them itself.
47. [ ] `src/laddrix/risk/access.py` — Who may still trade, checked when an order actually goes, not only when it was asked (#366).
48. [ ] `src/laddrix/risk/surveillance.py` — Surveillance alerts for the risk role (#291): patterns an exchange or a regulator would ask about, worked out on the server as orders happen.

**store/**

49. [ ] `src/laddrix/store/events.py` — The event log: every order event, append-only, in a crash-safe SQLite file.

**(top level)/**

50. [ ] `src/laddrix/platform.py` — Putting it together: one AccountRuntime per broker account, inside one Platform.
51. [ ] `src/laddrix/recovery.py` — After a restart: rebuild an account's trading state before it may trade again.

**server/**

52. [ ] `src/laddrix/server/security.py` — Security building blocks: passwords, 2FA codes, session tokens, secrets at rest.
53. [ ] `src/laddrix/server/users.py` — Users, broker accounts and login sessions, in a small SQLite file.
54. [ ] `src/laddrix/server/formats.py` — How positions, algos, instruments and audit events look in JSON: shared by the API and the stream so they never differ.
55. [ ] `src/laddrix/server/stream.py` — The live stream to the browser: one WebSocket per tab.
56. [ ] `src/laddrix/server/context.py` — What every part of the HTTP API shares: the server's settings, its services, and the checks each request goes through (who is asking, which account, which firm).
57. [ ] `src/laddrix/server/bodies.py` — The HTTP API's request bodies: unknown fields are refused, so nobody can smuggle in a user or account id; who is asking always comes from the session.
58. [ ] `src/laddrix/server/admin.py` — The admin API (K13.1): users, roles, broker accounts and each account's risk limits, without code or a terminal.

**server/routes/**

59. [ ] `src/laddrix/server/routes/session.py` — Logging in and out, the user's own password, step-up, sessions, layouts and settings, and the broker's own daily login (Kite's).
60. [ ] `src/laddrix/server/routes/ops.py` — Health, readiness, the status page, metrics, operations and a replay's controls (#287).
61. [ ] `src/laddrix/server/routes/market.py` — Prices and what is drawn from them: instruments, options, charts, market alerts, movers.
62. [ ] `src/laddrix/server/routes/trading.py` — Trading one account: orders, baskets, GTTs, algos, charges and margin.
63. [ ] `src/laddrix/server/routes/portfolio.py` — What an account holds and made: positions, end-of-day reports, P&L history, and the broker's funds, holdings and tradebook (read-only).
64. [ ] `src/laddrix/server/routes/risk.py` — Risk on an account and the firm: MTM limits, square-off, lost stops, flatten, the kill switches, surveillance, the auditor's export and trades made outside Laddrix.
65. [ ] `src/laddrix/server/routes/spreads.py` — Spreads (#414) and structure positions (#428): each user's multi-leg definitions, trading them with the Autospreader, and grouping an account's fills into structures.
66. [ ] `src/laddrix/server/routes/research.py` — Research (K11): recorded days of ticks, backtests over them, imported candles.

**server/**

67. [ ] `src/laddrix/server/app.py` — The HTTP API: login, accounts, orders, positions, kill switches, audit history.

**algos/**

68. [ ] `src/laddrix/algos/spec.py` — What an algo needs from the trader: the algo spec the screens are built from.
69. [ ] `src/laddrix/algos/engine.py` — The algo engine: parent orders worked by strategies through child orders.
70. [ ] `src/laddrix/algos/builtin.py` — The algos Laddrix ships with.
71. [ ] `src/laddrix/algos/execution.py` — What execution algos share: where to price a child, and keeping children at that price.
72. [ ] `src/laddrix/algos/slicer.py` — The slicer: a big order sent as small limit orders over time (time-sliced execution).
73. [ ] `src/laddrix/algos/iceberg.py` — The iceberg: a big order shown a little at a time, at the trader's limit price.
74. [ ] `src/laddrix/algos/pov.py` — POV (percentage of volume): trade a set share of what the market trades, until done.
75. [ ] `src/laddrix/algos/protect.py` — Protection held by Laddrix: trailing stop, OCO exit and bracket.
76. [ ] `src/laddrix/algos/schedule.py` — TWAP and VWAP: an order worked to a schedule over a time window.
77. [ ] `src/laddrix/algos/spreader.py` — The spreader: trade a two-leg spread (e.g.
78. [ ] `src/laddrix/algos/autospreader.py` — The Autospreader algo (ASE, #415): trade a multi-leg spread at a spread price.

**ops/**

79. [ ] `src/laddrix/ops/alerts.py` — Operational alerts: one place, each told once, to every place that should hear it.
80. [ ] `src/laddrix/ops/scheduler.py` — Run jobs at India times on trading days.
81. [ ] `src/laddrix/ops/checks.py` — The checks the scheduler runs, each raising or clearing one alert.
82. [ ] `src/laddrix/ops/guards.py` — On every screen (the alert band): MTM limits reached, and MIS positions before the cut-off.
83. [ ] `src/laddrix/ops/notify.py` — Send alerts off the machine: a JSON POST to a webhook (Slack, Google Chat, ntfy, or any service that relays to a phone).
84. [ ] `src/laddrix/ops/protection.py` — On every screen (the alert band): positions that had protection and have lost it (V1.2, #328).
85. [ ] `src/laddrix/ops/fence.py` — One trading server per data folder (#273): a second one can't trade the same accounts.
86. [ ] `src/laddrix/ops/logs.py` — Logs as JSON lines, each carrying the ids that tie it to a request and an order (#287).
87. [ ] `src/laddrix/ops/status.py` — One status for the whole server (#287): each part's state, why, and since when.
88. [ ] `src/laddrix/ops/supervisor.py` — Critical background loops, watched (#273): a living web server isn't a healthy trading one.

**charges/**

89. [ ] `src/laddrix/charges/model.py` — What a trade costs beyond the price: brokerage and the taxes and fees on Indian trades.
90. [ ] `src/laddrix/charges/schedules.py` — Charge schedules: rates as data, with the date they were checked.
91. [ ] `src/laddrix/charges/service.py` — One account's charges for the day, and estimates for orders not yet sent.

**charts/**

92. [ ] `src/laddrix/charts/candles.py` — Candles (OHLCV): built live from ticks, one level only (1 minute), added up for the rest.
93. [ ] `src/laddrix/charts/history.py` — Past candles: where a chart's history comes from (replaceable).
94. [ ] `src/laddrix/charts/service.py` — Charts: candles for a screen (history joined to live) and the account's trades as marks.

**alerts/**

95. [ ] `src/laddrix/alerts/engine.py` — Market alerts: price, % move, day volume, open interest and spread, checked on every tick.
96. [ ] `src/laddrix/alerts/service.py` — Market alerts for the server: the engine, kept per user, told to the screens, the audit trail and (when the user asked for it) their webhook.
97. [ ] `src/laddrix/alerts/store.py` — Alerts kept across restarts: one JSON file per user (their alerts and their webhook).
98. [ ] `src/laddrix/alerts/webhook.py` — Alerts sent to a user's webhook: signed, retried, never on the trading loop's time.

**spreads/**

99. [ ] `src/laddrix/spreads/model.py` — A multi-leg spread and its live implied price (#414, part of #250).
100. [ ] `src/laddrix/spreads/store.py` — Each user's spread definitions, kept across restarts (#414).
101. [ ] `src/laddrix/spreads/pricer.py` — Live implied spread prices, as ticks of "SPREAD:<id>" (#414).

**structures/**

102. [ ] `src/laddrix/structures/book.py` — Structure positions (#428, part of #250): spreads held, their P&L, and what is left over.

**options/**

103. [ ] `src/laddrix/options/chain.py` — The option chain: every strike of one underlying and expiry, priced and with Greeks.
104. [ ] `src/laddrix/options/model.py` — From instruments and live prices to the numbers pricing needs.
105. [ ] `src/laddrix/options/pricing.py` — Black-76: European options priced off the future, with implied volatility and Greeks.
106. [ ] `src/laddrix/options/strategy.py` — Option strategies: templates, legs at live prices, and what the position does.

**portfolio/**

107. [ ] `src/laddrix/portfolio/service.py` — What an account holds and owns, kept fresh from its broker: funds, holdings, trades.

**reports/**

108. [ ] `src/laddrix/reports/daily.py` — The end-of-day report for one account, built from the audit trail (the source of truth).
109. [ ] `src/laddrix/reports/export.py` — A day's report as a download: CSV (for a spreadsheet) or PDF (to read and keep).
110. [ ] `src/laddrix/reports/history.py` — P&L over many days, from kept end-of-day reports: by day, by instrument, by strategy.
111. [ ] `src/laddrix/reports/pdf.py` — A small PDF writer for text reports: monospaced lines on A4 pages, no dependency.
112. [ ] `src/laddrix/reports/service.py` — Reports for the screens: a day's report (kept after the close, or a live preview), P&L history, and the 16:00 job that keeps each account's report.
113. [ ] `src/laddrix/reports/store.py` — Kept reports: one JSON file per account and day, written after the close.

**research/**

114. [ ] `src/laddrix/research/backtest.py` — Backtests: an algo over recorded days, with the same engine, paper broker and risk checks as live trading, so a backtest can't be kinder than the market was.
115. [ ] `src/laddrix/research/replay.py` — Replay a recorded day (K11.1) into MarketData, so paper trading runs on a real past day.
116. [ ] `src/laddrix/research/ticks.py` — Recorded ticks: every tick Laddrix receives, kept for replay (K11.2) and backtests (K11.3).

**broker/kite/**

117. [ ] `src/laddrix/broker/kite/broker.py` — KiteBroker: real orders through Kite Connect, behind the same Broker interface.
118. [ ] `src/laddrix/broker/kite/daily_login.py` — Kite's daily login, started from the web page instead of the terminal.
119. [ ] `src/laddrix/broker/kite/decoder.py` — Decode Kite's binary WebSocket frames into ticks keyed by Kite's instrument token.
120. [ ] `src/laddrix/broker/kite/encode.py` — Build Kite binary frames (big-endian, paise), exactly as Kite sends them.
121. [ ] `src/laddrix/broker/kite/feed.py` — Glue between Kite's ticker and Laddrix: Kite tokens in, our ticks out.
122. [ ] `src/laddrix/broker/kite/gtt.py` — Kite's GTT API (/gtt/triggers) in Laddrix's terms.
123. [ ] `src/laddrix/broker/kite/http.py` — A small async client for Kite Connect's REST API (v3).
124. [ ] `src/laddrix/broker/kite/instruments.py` — Load Kite's daily instrument dump (CSV) into the instrument master.
125. [ ] `src/laddrix/broker/kite/live.py` — Live Kite inside the server: each kite account's connection, from start-up to 6 AM.
126. [ ] `src/laddrix/broker/kite/orders.py` — Translating orders between Laddrix and Kite (pure functions, no network).
127. [ ] `src/laddrix/broker/kite/portfolio.py` — Kite's holdings, margins and tradebook in Laddrix's terms.
128. [ ] `src/laddrix/broker/kite/session.py` — Kite login: turn the account holder's manual login into an access token.
129. [ ] `src/laddrix/broker/kite/ticker.py` — Kite's live WebSocket: prices (binary) and order updates (JSON text), kept alive forever.
130. [ ] `src/laddrix/broker/kite/token_store.py` — Keep today's Kite access token in a private local file between commands.

**tools/**

131. [ ] `src/laddrix/tools/demo.py` — Run the whole platform locally with simulated prices and paper trading: no Kite needed.
132. [ ] `src/laddrix/tools/serve.py` — Run Laddrix as a server: the entry point the Docker image starts.
133. [ ] `src/laddrix/tools/amo_check.py` — Go-live check: place ONE after-market order (AMO) through Kite, then cancel it.
134. [ ] `src/laddrix/tools/backup.py` — Back up everything a restored Laddrix server needs, safely while the server runs (V1.3).
135. [ ] `src/laddrix/tools/loadtest.py` — Load test: the real server in its own process, fed by a fake Kite, read by screen-like clients.
136. [ ] `src/laddrix/tools/login.py` — Log in to Kite from the terminal: python -m laddrix.tools.login
137. [ ] `src/laddrix/tools/restore.py` — Restore a backup into a data folder (V1.3).
138. [ ] `src/laddrix/tools/users.py` — Manage Laddrix users and accounts from the server's terminal.
139. [ ] `src/laddrix/tools/watch.py` — Watch a live ladder in the terminal: python -m laddrix.tools.watch NFO:NIFTY26OCTFUT
140. [ ] `src/laddrix/tools/audit.py` — Check that the audit trail is whole (#367).

**(top level)/**

141. [ ] `src/laddrix/config.py` — Settings read from the environment (and an optional .env file for local use).
142. [ ] `src/laddrix/metrics.py` — Measurements: how long each step takes, as histograms, so speed is a number, not a guess.
143. [ ] `src/laddrix/version.py` — Laddrix's version (V1.6, #289): one place, shown by /api/health and on the screens.
144. [ ] `src/laddrix/logsafe.py` — Logs never hold a secret (#367).

## Part B · The screens (TypeScript/React), 92 files

**api/**

1. [ ] `ui/src/api/rest.ts` — Talking to the server's REST API.
2. [ ] `ui/src/api/stream.ts` — The one WebSocket per tab (docs/design/stream.md).
3. [ ] `ui/src/api/types.ts` — The shapes the server sends (docs/design/stream.md and the REST API).

**app/**

4. [ ] `ui/src/app/context.tsx` — The services every widget shares, and the hooks widgets use to read them.

**state/**

5. [ ] `ui/src/state/gate.ts` — "Nothing is sent blind" (docs/design/ui.md §1): before any order click, why it can't go.
6. [ ] `ui/src/state/instruments.ts` — Instrument details (tick size, lot size, name) fetched once and shared by every widget.
7. [ ] `ui/src/state/orderSpecs.ts` — Order specs (what can be ordered on an instrument through an account) fetched once per account + instrument and shared by every ticket and ladder.
8. [ ] `ui/src/state/session.ts` — The session: logging in and out, and keeping the Store in step with the server.
9. [ ] `ui/src/state/store.ts` — Everything the screens show that isn't a price: who is logged in, orders, positions, kill switches, position alerts and the Messages list.

**lib/**

10. [ ] `ui/src/lib/format.ts` — How numbers, money and times are written on screen.

**theme/**

11. [ ] `ui/src/theme/theme.ts` — The screen's theme (K13.5): dark, light or colour-blind safe, or "system" (follow the computer's light/dark setting) until the user chooses.

**components/**

12. [ ] `ui/src/components/Menu.tsx` — A small drop-down: a toolbar button that opens a list.
13. [ ] `ui/src/components/QrCode.tsx` — A QR code drawn as an SVG, for the authenticator link an admin hands to a new user (scan it with the phone's authenticator app).
14. [ ] `ui/src/components/SearchBox.tsx` — Find an instrument by symbol: type, arrow through the matches, Enter to pick.
15. [ ] `ui/src/components/ContextMenu.tsx` — The app's own right-click menu (#412): a list of items, some with a submenu (▸), drawn at the pointer and never off screen.
16. [ ] `ui/src/components/ExpiryBadge.tsx` — Expiry warnings (#290): the server says when a contract ends and how it settles; this only shows it.

**shell/**

17. [ ] `ui/src/shell/Bands.tsx` — The bands under the toolbar: only when something needs attention, most serious first (docs/design/ui.md §3).
18. [ ] `ui/src/shell/EnvStrip.tsx` — The strip under the toolbar: which world orders go to (paper, live, none), and for a broker with a daily login that hasn't happened today, the button that starts it.
19. [ ] `ui/src/shell/Login.tsx` — Sign in: username, password and the 6-digit authenticator code.
20. [ ] `ui/src/shell/ReplayBar.tsx` — The replay bar: shown only on a replay server (K11.2), where prices come from a recorded day.
21. [ ] `ui/src/shell/RestoreStop.tsx` — "Restore stop" in an UNPROTECTED band (#264): starts the stop the position had again, sized to what is open now, after two clicks.
22. [ ] `ui/src/shell/ServerVersion.tsx` — The running server's version in the status bar (V1.6, #289), as the server says it (GET /api/health): after a deploy or a rollback, everyone can see which version they're on.
23. [ ] `ui/src/shell/SetPassword.tsx` — "Set your password": shown instead of the terminal when an admin made this user, or reset their password, with a one-time password (K13.2).
24. [ ] `ui/src/shell/Shell.tsx` — The terminal around the widgets:
25. [ ] `ui/src/shell/StopBand.tsx` — The STOP band (#262): what the STOP did, in the server's own words, and Flatten.
26. [ ] `ui/src/shell/StopDialog.tsx` — STOP and resume: the only dialogs in the terminal (docs/design/ui.md §5).
27. [ ] `ui/src/shell/ThemePick.tsx` — The theme picker in the top bar (K13.5): the new colours show at once and are saved for the user; if the server refuses, the old theme comes back and the reason is said.
28. [ ] `ui/src/shell/SessionsDialog.tsx` — Your own sessions (#363): every browser or app logged in as you, with when and from where, and an End button for each.
29. [ ] `ui/src/shell/StepUpDialog.tsx` — A fresh authenticator code for a privileged change (#363).

**workspace/**

30. [ ] `ui/src/workspace/Panel.tsx` — The frame around every widget: hands the widget its saved params, saves changes, follows the link group (WidgetBody, shared with free windows), and for docked tiles keeps the…
31. [ ] `ui/src/workspace/Workspace.tsx` — The desktop: the showing page (free windows on a scrolling canvas, or dockview's docked tiles), plus the page tabs along the bottom (Main | Options | … | +), like a trading…
32. [ ] `ui/src/workspace/controller.ts` — The workspace behind the screen: its pages (Main, Options, …), which one is showing, and saving.
33. [ ] `ui/src/workspace/defaults.ts` — The workspace a new user starts with: a Watch list on the left, ladders for the first futures in the middle, and the order book, positions and audit trail (Messages in a tab…
34. [ ] `ui/src/workspace/layouts.ts` — Saving and restoring the workspace: which widgets are open, where, and with what settings.
35. [ ] `ui/src/workspace/links.ts` — Link groups: widgets with the same colour follow the same instrument.
36. [ ] `ui/src/workspace/registry.ts` — Every kind of widget the workspace can open.
37. [ ] `ui/src/workspace/FreeCanvas.tsx` — A free page (#410): every widget is a window you drag by its title bar and resize from any edge or corner.
38. [ ] `ui/src/workspace/canvas.ts` — A free page: widgets are windows on a canvas, each with its own place and size, with empty space allowed between them.
39. [ ] `ui/src/workspace/menus.ts` — What the right-click menu offers (#412).

**keys/**

40. [ ] `ui/src/keys/bindings.ts` — The user's keyboard shortcuts, kept on the server (GET/PUT /api/me/settings/keys) so they follow the user to another machine.
41. [ ] `ui/src/keys/model.ts` — Keyboard shortcuts (K13.4): one list of every action a key can start, each with its default key, and the user's own choices on top (saved on the server, so they follow the user).

**widgets/books/**

42. [ ] `ui/src/widgets/books/AuditTrail.tsx` — Audit trail: everything that happened on the account, newest first, append-only (canvas boards 2 and 6, docs/design/ui.md §4).
43. [ ] `ui/src/widgets/books/Messages.tsx` — Messages: every refusal, warning and notice, newest first (docs/design/ui.md §4).
44. [ ] `ui/src/widgets/books/OrderBook.tsx` — Order book: every order on the account, live, in tabs (canvas board 2, docs/design/ui.md §4).
45. [ ] `ui/src/widgets/books/Positions.tsx` — Positions: net quantity and P&L per contract, live, with a total row and whether Laddrix's positions match the broker's (canvas board 2, docs/design/ui.md §4).
46. [ ] `ui/src/widgets/books/SquareOff.tsx` — The square-off assistant in the Positions bar: open intraday (MIS) positions before the broker's own square-off.
47. [ ] `ui/src/widgets/books/model.ts` — Rules shared by the order book, positions and audit trail, kept apart from React so each can be tested on its own.

**widgets/ladder/**

48. [ ] `ui/src/widgets/ladder/Ladder.tsx` — The ladder: the trader's main order entry (docs/design/ui.md §4, canvas board 0).
49. [ ] `ui/src/widgets/ladder/model.ts` — The ladder's rules, kept apart from React so each one can be tested on its own.

**widgets/ticket/**

50. [ ] `ui/src/widgets/ticket/Ticket.tsx` — Order ticket: every order type from a form (docs/design/ui.md §4, canvas board 3).
51. [ ] `ui/src/widgets/ticket/model.ts` — The order ticket's rules (docs/design/ui.md §4 "Order ticket"), apart from React so each can be tested on its own.
52. [ ] `ui/src/widgets/ticket/useChargeEstimate.ts` — What an order would pay in charges, and the margin it would block, if it filled in full: asked from the server (it holds the rates and asks the broker), a moment after the…

**widgets/**

53. [ ] `ui/src/widgets/Watch.tsx` — Watch list: instruments with live last price and change.

**widgets/admin/**

54. [ ] `ui/src/widgets/admin/Admin.tsx` — Admin: users, broker accounts and each account's risk limits, without the server's terminal (K13.2; the API is K13.1, docs/design/admin.md).
55. [ ] `ui/src/widgets/admin/model.ts` — Admin screen logic (K13.2): the limits form from the server's limits spec, what a user's or an account's state reads as, and the authenticator link's parts.

**widgets/alerts/**

56. [ ] `ui/src/widgets/alerts/AlertsManager.tsx` — Alerts: your price, % move, volume, OI and spread alerts (K12.4; the engine is K12.3, docs/design/charts.md).
57. [ ] `ui/src/widgets/alerts/model.ts` — Alerts manager logic (K12.4): the create request from the server's alert spec, what an alert says in words, and its state's tone.

**widgets/ase/**

58. [ ] `ui/src/widgets/ase/model.ts` — The Autospreader's form, as pure functions (#414): a draft of a spread's name and legs, the small conveniences (the price multiplier follows the ratio; one quoting leg), and…
59. [ ] `ui/src/widgets/ase/Autospreader.tsx` — The Autospreader (#414): define spreads of 2 to 6 legs and see each one's live implied price.

**widgets/algos/**

60. [ ] `ui/src/widgets/algos/AlgoManager.tsx` — Algo manager: start, watch and control algos (docs/design/algos.md, K6.4).
61. [ ] `ui/src/widgets/algos/model.ts` — Algo manager logic, kept out of the widget so it can be tested on its own.

**widgets/chain/**

62. [ ] `ui/src/widgets/chain/OptionChain.tsx` — Option chain: calls on the left, strikes in the middle, puts on the right, live (K8.3).
63. [ ] `ui/src/widgets/chain/model.ts` — Option chain logic, kept out of the widget so it can be tested on its own.

**widgets/chart/**

64. [ ] `ui/src/widgets/chart/Chart.tsx` — Chart: candles with a volume pane, the live forming candle, the account's fills as marks, and click-a-price to fill in the linked ticket (K12.2; the data is K12.1,…
65. [ ] `ui/src/widgets/chart/draw.ts` — Drawing a chart on a canvas: candles (up hollow, down filled: shape as well as colour, so colour-blind readers and greyscale prints read them), the volume pane, price and time…
66. [ ] `ui/src/widgets/chart/model.ts` — Chart logic, kept out of the widget so it can be tested without a canvas: merging candles (history, older pages, the forming one), the view (how many bars, how far from the…

**widgets/gtt/**

67. [ ] `ui/src/widgets/gtt/GttBook.tsx` — GTT book: orders the BROKER holds until a price is touched (docs/design/gtt.md, K9.3/K9.4).
68. [ ] `ui/src/widgets/gtt/model.ts` — GTT form rules (docs/design/gtt.md), apart from React so each can be tested on its own.

**widgets/**

69. [ ] `ui/src/widgets/index.ts` — Importing a widget registers it with the workspace (Widgets ▾ and saved layouts).

**widgets/keys/**

70. [ ] `ui/src/widgets/keys/KeysEditor.tsx` — Keyboard shortcuts: every action a key can start, by where it works, with its keys (K13.4; docs/design/ui.md).

**widgets/market/**

71. [ ] `ui/src/widgets/market/Depth.tsx` — Market depth: the book's levels for one contract, with bars, running totals, the whole book's waiting quantity, the imbalance and the spread.
72. [ ] `ui/src/widgets/market/Movers.tsx` — Movers: the biggest gainers, losers, volume and open-interest change among the contracts the server has prices for, ranked by the server (GET /api/market/movers), read every 2 s.
73. [ ] `ui/src/widgets/market/TimeAndSales.tsx` — Time & sales: the trades between price snapshots for one contract, newest on top, with the side estimated from the touch before each trade (the server works them out from every…
74. [ ] `ui/src/widgets/market/model.ts` — Market screens logic (K12.4): the depth window's numbers and the time & sales tape.

**widgets/mtm/**

75. [ ] `ui/src/widgets/mtm/MtmLimits.tsx` — MTM Limits: the trader's own loss limits, per position and for the whole account, watched by the server on every price (risk/mtm.py).

**widgets/portfolio/**

76. [ ] `ui/src/widgets/portfolio/FundsPanel.tsx` — Funds: what the account can still use, what is blocked, and why, per segment, as the broker reports it (paper: worked out by Laddrix, marked "estimate").
77. [ ] `ui/src/widgets/portfolio/HoldingsPanel.tsx` — Holdings: delivery shares kept from earlier days, at live prices, with P&L and the day's change, and a total row (the broker's holdings; today's delivery buys are positions).
78. [ ] `ui/src/widgets/portfolio/TradesTable.tsx` — The tradebook (the order book's Trades tab): every fill at the exchange today, newest first, from the broker.
79. [ ] `ui/src/widgets/portfolio/model.ts` — Funds, holdings and trades as screens show them.
80. [ ] `ui/src/widgets/portfolio/usePortfolio.ts` — One kind of portfolio figures (funds, holdings or trades) for the selected account, with a Refresh that asks the broker now.

**widgets/reports/**

81. [ ] `ui/src/widgets/reports/Reports.tsx` — Reports: the end-of-day report of a day (kept after the close, or today's preview) and the P&L history by day, instrument or strategy, with CSV and PDF downloads.
82. [ ] `ui/src/widgets/reports/model.ts` — Reports as screens show them: which days can be picked, where downloads come from, how a report's state is said.

**widgets/research/**

83. [ ] `ui/src/widgets/research/Backtests.tsx` — Backtests: start an algo over recorded days, follow the run, read its results, compare runs (K11.4; the runner is K11.3, docs/design/research.md).
84. [ ] `ui/src/widgets/research/LineChart.tsx` — A small SVG line chart for research curves (equity over days, P&L through a day).
85. [ ] `ui/src/widgets/research/Recordings.tsx` — Recordings: the days of ticks this server has kept (K11.1), for replays and backtests.
86. [ ] `ui/src/widgets/research/model.ts` — Research screens logic (K11.4), kept out of the widgets so it can be tested on its own: sizes and states in words, the backtest request built from the server's form, and the…

**widgets/strategy/**

87. [ ] `ui/src/widgets/strategy/StrategyBuilder.tsx` — Strategy builder: pick a template (or Alt-click legs in the option chain), edit the legs, and see what the position does before trading it (K8.4).
88. [ ] `ui/src/widgets/strategy/model.ts` — Strategy builder logic: the payoff chart's geometry and the numbers' words.

**(top level)/**

89. [ ] `ui/src/main.tsx` — The entry point: loads the styles and every widget, then draws the app into the page's #root.
90. [ ] `ui/src/App.tsx` — The page: checks the session, then shows the sign-in, the "set your password" page (after an admin's reset) or the terminal.
91. [ ] `ui/src/node-fs.d.ts` — The one Node call the tests make (the theme tests read the stylesheets as text); the app itself runs in the browser, so Node's full type package isn't installed.

**widgets/structures/**

92. [ ] `ui/src/widgets/structures/Structures.tsx` — Structure positions (#428): the spreads an account holds, from the fills allocated to them.

