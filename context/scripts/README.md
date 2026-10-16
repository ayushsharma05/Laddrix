# Check scripts (used by Claude in cloud sessions)

| Script | What it does | Run |
| --- | --- | --- |
| `e2e-ladder.cjs` | Real browser (Playwright): log in to the demo server, place/move/cancel from the ladder, a marketable buy fills, position updates, screenshots | `node context/scripts/e2e-ladder.cjs <out-dir>` |
| `e2e-books.cjs` | Real browser: orders from the ladder, then order book tabs, positions, audit trail + timeline, cancel from the order book, Messages | `node context/scripts/e2e-books.cjs <out-dir>` |
| `e2e-ticket.cjs` | Real browser: resets the saved layout, then LMT, MKT, SL-M and SL from the order ticket (including a refused stop) | `node context/scripts/e2e-ticket.cjs <out-dir>` |
| `e2e-algos.cjs` | Real browser: starts a slicer and a spreader from the algo form, expands the spread, pause/resume/stop, reads Messages | `node context/scripts/e2e-algos.cjs <out-dir>` |
| `mutate-algo-ui.py` | 12 mutations of the algo screen, its model and the store; each must fail a test | `cd ui && python3 ../context/scripts/mutate-algo-ui.py` |
| `e2e-compose.cjs` | Real browser through Caddy (`https://localhost`, compose stack up): log in with the user's TOTP secret, buy from the ladder, positions and audit | `node context/scripts/e2e-compose.cjs <totp-secret>` |
| `e2e-kite-login.cjs` | Real browser against `serve` in kite mode (no Kite needed): the strip says login is needed, and the button goes to Zerodha with api_key and state | `node context/scripts/e2e-kite-login.cjs <totp-secret>` |
| `mutate-kite-live.py` | 12 mutations of `broker/kite/live.py`; each must fail `tests/test_kite_live.py` | `python3 context/scripts/mutate-kite-live.py` |
| `mutate-pricing.py` | 16 formula mutations of `options/`; each must fail `tests/test_option_pricing.py` | `python3 context/scripts/mutate-pricing.py` |
| `e2e-chain.cjs` | Real browser: open the Option Chain (Widgets), maximize/restore, click a put to drive the ticket, switch expiry | `node context/scripts/e2e-chain.cjs <out-dir>` |
| `e2e-strategy.cjs` | Real browser: Strategy Builder, iron condor, payoff and figures, Alt-click a leg from the chain | `node context/scripts/e2e-strategy.cjs <out-dir>` |
| `e2e-exec-algos.cjs` | Real browser: TWAP, iceberg (limit = TWAP price + ₹20) and POV from the algo form, each to Done. Needs a fresh demo (positions add up to the ±1500 limit) | `node context/scripts/e2e-exec-algos.cjs <out-dir>` |
| `mutate-exec-algos.py` | 17 mutations of the K9.1 algos | `python3 context/scripts/mutate-exec-algos.py` |
| `mutate-strategy.py` | 13 mutations of `options/strategy.py` | `python3 context/scripts/mutate-strategy.py` |
| `e2e-popout.cjs` | Real browser: pop a ladder out, check it updates live, trade from it, dock it back | `node context/scripts/e2e-popout.cjs <out-dir>` |
| `ui/desktop/e2e/desktop-smoke.cjs` | The real Electron app under a display: login, pop-out, Node-access probe, memory | `cd ui/desktop && xvfb-run -a node e2e/desktop-smoke.cjs <out-dir>` |
| `mutate-algo.py` | Breaks each algo-engine safety rule and checks a test fails | `python3 context/scripts/mutate-algo.py` (repo root) |
| `mutate-ladder.py` | Breaks each ladder rule on purpose and checks a test fails (always restores the file) | `cd ui && python3 ../context/scripts/mutate-ladder.py` |
| `mutate-books.py` | Same for the K5.5 screens | `cd ui && python3 ../context/scripts/mutate-books.py` |

Before the e2e scripts: build the UI and start the demo server (fresh data = the default layout):

```bash
cd ui && npm ci && npm run build && cd ..
rm -rf data/demo
python3 -m mocktrade.tools.demo --ui ui/dist      # background; http://localhost:8000, demo / demo-password-123
```

The scripts assume the cloud container's paths (`/home/user/MockTrade`, Playwright at `/opt/node22/lib/node_modules/playwright`, Chromium at `/opt/pw-browsers/chromium-1194`); adjust elsewhere. Stop the demo with `pkill -f "^python3 -m mocktrade.tools.demo"` (the `^` stops it matching the shell's own command line, which killed the shell twice).

TOTP codes work only once (replay protection) and 5 failures lock the user for 15 minutes:
scripts must use a fresh code each login, and `python -m mocktrade.tools.demo --code` must run
from the repo root (elsewhere it creates a second demo user with a different secret).
| `mutate-portfolio.py` | 27 mutations of funds, holdings and the tradebook (paper money, recovery hand-over, service, Kite parsing); 25 caught, 2 equivalent | `python3 context/scripts/mutate-portfolio.py` (alone) |
| `e2e-portfolio.cjs` | Real browser on a fresh demo: funds before/after a 75-lot buy, the tradebook, holdings, then the Trades tab, Funds and Holdings on screen | `node context/scripts/e2e-portfolio.cjs` |
| `mutate-charges.py` | 20 mutations of the charges model, the schedule and the service; all caught | `python3 context/scripts/mutate-charges.py` (alone) |
| `e2e-charges.cjs` | Real browser on a fresh demo: the published schedule, a futures round trip, the day's charges, Positions gross/charges/net and the ticket estimate | `node context/scripts/e2e-charges.cjs` |
| `mutate-reports.py` | 20 mutations of the report (carried in, each fill once, marks, strategies, alerts, history, CSV/PDF, store, day bounds); all caught | `python3 context/scripts/mutate-reports.py` (alone) |
| `e2e-reports.cjs` | Real browser on a fresh demo: a futures round trip, the Reports tab (preview follows fills), CSV and PDF downloaded and read back, History | `node context/scripts/e2e-reports.cjs` |
| `mutate-margin.py` | 15 mutations of the margin gate, the paper quote and the wiring; all caught | `python3 context/scripts/mutate-margin.py` (alone) |
| `e2e-margin.cjs` | Real browser on a fresh demo: ticket margin line (1 and 5 lots), 2+2 lots bought, 1 more refused with both numbers, the exits accepted | `node context/scripts/e2e-margin.cjs` |
| `mutate-mtm.py` | 18 mutations of the MTM watch, the square-off assistant, closing and the band; all caught | `python3 context/scripts/mutate-mtm.py` (alone) |
| `e2e-mtm.cjs` | Real browser on a fresh demo: Square off MIS with its confirmation; an MTM ₹1 limit with auto-close set on the MTM Limits tab closes the position; the message | `node context/scripts/e2e-mtm.cjs` |
| `mutate-recorder.py` | 14 mutations of the tick recorder and store (fields, India day, append, merge order, crash cut, rotation, retry, the tap); all caught | `python3 context/scripts/mutate-recorder.py` (alone) |
| `check-recording.py` | Reads a real recording (the demo's) back: ticks, bytes per tick, busiest instruments, read speed | `python3 context/scripts/check-recording.py [data/demo/ticks]` |
| `mutate-replay.py` | 11 mutations of the replay feed (pace, cap, loop turns, pause, clock, seek, speeds) and the paper broker's every-tick mode; all caught | `python3 context/scripts/mutate-replay.py` (alone) |
| `e2e-replay.cjs` | Real browser on a replay server (`demo --replay <day> --port 8001`, after the demo recorded that day): bar and band, the replayed time moving, an order filled, pause, speed, jump | `node context/scripts/e2e-replay.cjs <day>` |
| `mutate-backtest.py` | 12 mutations of the backtest runner (start time, heartbeat, drain, candle path and spread, drawdown, winning days, restart, checks); all caught | `python3 context/scripts/mutate-backtest.py` (alone) |
| `check-backtest.py` | A real backtest over a recording (the demo's): state, time taken, summary, trades | `python3 context/scripts/check-backtest.py [ticks dir] [ALGO] [INSTRUMENT]` |
| `mutate-research.py` | 25 mutations of the research screens and their API (form, day downloads, skipped days, polling, re-reading a run, compare slots, chart, day picks); all caught | `python3 context/scripts/mutate-research.py` (alone) |
| `e2e-research.cjs` | Real browser on the demo (after it has recorded a few seconds): Recordings, a TWAP backtest from the New form followed to done, totals/curve/day/hover, CSV and PDF saved, a second run, Compare | `node context/scripts/e2e-research.cjs` |
| `mutate-recordings.py` | 13 mutations of deleting recorded days (today, India day, admin, audit, 409) and the screen (confirm step, refresh, copy); all caught | `python3 context/scripts/mutate-recordings.py` (alone) |
| `e2e-recordings.cjs` | Real browser on the demo with an older day copied in: delete it after confirming (files gone, audit row), today offers no delete, the replay command copies | `node context/scripts/e2e-recordings.cjs [older day]` |
| `mutate-charts.py` | 23 mutations of candles (buckets, volume, quotes, new day, late ticks, trimming, OI), the history/live join, Kite split/spacing/cache, recordings, the stream limit and throttle, the API; all caught | `python3 context/scripts/mutate-charts.py` (alone) |
| `check-charts.py` | The chart API on a running demo: spec, 1m/5m candles, one minute checked by hand against the recording, a real fill as a mark, the forming candle on the stream | `python3 context/scripts/check-charts.py [instrument]` |
| `mutate-chart-ui.py` | 32 mutations of the chart model, drawing and widget (merge, view, scales, labels, marks, drag, races, picks), the ticket's price pick, the candle stream and the tab title; all caught | `python3 context/scripts/mutate-chart-ui.py` (alone) |
| `e2e-chart.cjs` | Real browser on the demo: a Chart from Widgets, pixels painted, the live candle, a real fill marked, a click filling the pink ticket's price, tab titles, 1m zoom and drag-pan | `node context/scripts/e2e-chart.cjs` |
| `mutate-alerts.py` | 26 mutations of the alert engine (crossing, arming, cool-down, once, directions, close/OI waits, index, watch, checks, per-user), webhooks (public-address rules, retries, signature, cap) and the stream; all caught | `python3 context/scripts/mutate-alerts.py` (alone) |
| `check-alerts.py` | Alerts on a running demo: spec, an alert just above the live price firing on the stream, already-true ones, a spread alert, the audit rows, the webhook test | `python3 context/scripts/check-alerts.py [echo URL]` |
| `mutate-storage.py` | 14 mutations of safe file names (#269): the candle store's id rule and containment (a planted link too), row checks, whole-or-nothing writes, day files only, the import API's known-contract check and audit, account ids; all caught | `python3 context/scripts/mutate-storage.py` (alone) |
| `mutate-admin.py` | 41 mutations of the admin API: the limits spec (lists, kinds, looser or tighter, warnings, saved limits), a turned-off account, limits reaching every copy, a late account's loops, the last-admin and self-off guards, password resets and the first-login gate, account changes, the older users file; all caught | `python3 context/scripts/mutate-admin.py` (alone) |
| `check-admin.py` | The admin API on a running demo: a new user's first login and password change, a new paper account that trades at once, a limit preview and change met by the next order, a turned-off account, the audit rows; `--after-restart` checks the account and limit came back | `python3 context/scripts/check-admin.py [--after-restart]` |
| `mutate-market.py` | 39 mutations of time & sales (baseline, side at the touch, watched only, after/limit), movers (filters, ranking, OI day baseline, limits), the simulator's option day, the depth and tape models, the tape and movers polling, the alerts form checks and list, the hooks that keep a ticket on its contract, and the alert message; 39/39 caught | `python3 context/scripts/mutate-market.py` (alone) |
| `e2e-market.cjs` | The market screens in a browser on the demo: depth, time & sales, movers, the linked ticket after a switch, an alert made and fired, no page errors | `node context/scripts/e2e-market.cjs` (demo with `--ui ui/dist` running) |
