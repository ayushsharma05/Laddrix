# MockTrade status and next steps

As of 14 Oct 2026 (evening). GitHub issues are the source of truth.

Ladder (K5.4) as built: ![K5.4 ladder](images/k5-4-ladder.png)

Order book, positions, audit trail (K5.5) as built: ![K5.5 screens](images/k5-5-screens.png)

## Active track: Kite Connect (NSE/BSE)
Decided 14 Oct 2026. MockTrade connects to the real Indian market through Zerodha Kite Connect. A paper broker fills orders against live Kite prices for testing without money. Design: `docs/design/kite-architecture.md` (diagrams in `context/images/kite-*.png`).

| Milestone | Issues | Outcome |
| --- | --- | --- |
| K0 · Foundations | #124–#127 | package, money in paise, instruments, order types, broker interface |
| K1 · Live prices | #128–#132 | Kite login, instrument list, binary tick decoder, WebSocket client, live terminal ladder |
| K2 · Paper broker | #133–#135 | fills vs live depth, queue position, latency, Kite-like rejects |
| K3 · Orders & risk | #136–#140 | order manager, throttle 8/s · 350/min · 4500/day, risk + kill switch, SQLite event log, real Kite adapter |
| K4 · Positions & P&L | #141–#142 | P&L from fills, reconcile with Kite |
| K5 · Server & screens | #143–#147, #156 | FastAPI + password/TOTP, browser WebSocket, **widget workspace** (dockview), ladder, blotter/positions/audit, **Electron desktop app** |
| K6 · Algos | #148–#151 | parent/child engine, slicer, two-leg spread, algo screen |
| K7 · Deploy & operate | #152–#155 | Docker Compose + Caddy, Lightsail Mumbai + static IP, latency measurement, runbooks |

Issue bodies were created by `context/backlog-scripts/kite_backlog.py` (+ `create_kite.py`). #145 was later rewritten (widget workspace) and #156 added (Electron) after Ayush asked for a desktop, widget-based front end.

## Build progress (Claude now writes the code, at Ayush's request on 14 Oct)
Ayush asked Claude to code the whole project professionally (extensive tests, careful code) while he follows along; he types "continue" when usage limits reset. PRs are stacked: each milestone's branch builds on the previous one.

| Milestone | PR | Status |
| --- | --- | --- |
| K0 Foundations (#124–#127) | #158 (base main) | ✅ CI green, 50 tests |
| K1 Live prices (#128–#132) | #159 (base k0-foundations) | ✅ CI green, 117 tests |
| K2 Paper broker (#133–#135) | #160 (base k1-live-prices) | ✅ 167 tests; each fill rule mutation-checked |
| K3.1 Order manager (#136) | #161 (base k2-paper-broker) | ✅ CI green, 52 tests, 15 mutations caught |
| K3.2 Throttle (#137) | #162 (base k3-1-order-manager) | ✅ CI green, 23 tests |
| K3.3 Risk checks + kill switch (#138) | #163 (base k3-2-throttle) | ✅ CI green, 48 tests |
| K3.4 Event log (#139) | #164 (base k3-3-risk) | ✅ CI green, 16 tests |
| K3.5 Kite order adapter (#140) | #165 (base k3-4-event-log) | ✅ CI green, 370 tests in total. Manual step left: AMO check on the server |
| K4.1 Positions & P&L + auto kill on daily loss (#141) | #166 (base k3-5-kite-orders) | ✅ CI green, 43 tests |
| K4.2 Reconcile positions with the broker (#142) | #167 (base k4-1-positions) | ✅ 424 tests in total |
| K5.1 Server + password/TOTP login + API (#143) | #168 (base k4-2-reconcile) | ✅ CI green; 21 security mutations checked |
| K5.2 Browser stream (#144) | #169 (base k5-1-server) | ✅ CI green, 490 tests; contract in docs/design/stream.md |
| UI design pass (mockups) | canvas "MockTrade UI design" (claude.ai artifact) | ✅ dense trading terminal; ladder (board 0, interactive) + workspace (board 2) redone; other boards still "old style" |
| K5.3 UI skeleton: workspace, login, watch list (#145) | #170 (base k5-2-stream) | ✅ CI green after a fix: `ui/src/lib/format.ts` had never been pushed (the Python `.gitignore` ignored every `lib/` folder); rebuilt with tests, `.gitignore` now ignores only `/lib/` |
| K5.4 Ladder (#146) | #171 (base k5-3-ui) | ✅ CI green; 112 UI + 509 Python tests, 13/13 mutations caught, paper trading checked end to end in a real browser |
| K5.5 Order book, positions, audit trail, messages (#147) | #172 (base k5-4-ladder) | ✅ CI green; 143 UI + 511 Python tests, 13/13 mutations caught, checked end to end in a real browser |
| Order ticket + **order spec** (#173) | #175 (base k5-5-screens, branch k5-5b-ticket) | ✅ 179 UI + 529 Python tests; all order types checked in a real browser |
| K5.6 Desktop app + pop-out windows (#156) | #176 (base k5-5b-ticket, branch k5-6-desktop) | ✅ 181 UI + 12 desktop + 529 Python tests; real Electron run under xvfb (pop-out live, pages can't reach Node, ~600 MB app, ~20–25 MB per pop-out). Ayush: build the installer on Windows (`ui/desktop/README.md`) |
| Stream sends changes at once (#177) | #178 (base k5-6-desktop, branch k5-7-fast-stream) | ✅ tick → browser ~200 ms → **1–6 ms**; the new latency test fails on the old code |
| K6.1 Algo engine (#148) | #179 (base k5-7-fast-stream, branch k6-1-algo-engine) | ✅ 545 Python tests, 12/12 mutations, paper-broker integration test |

K3 flow: ![K3 orders and risk](images/k3-orders-and-risk.png)

Merge order: #123 → #157 → #158 → #159 → #160 → #161 → #162 → #163 → #164 → #165 → #166 → #167 → #168 → #169 → #170 → #171 → #172 → #175 → #176 → #178 → #179 → … (GitHub retargets stacked PRs automatically when their base merges).

**Design changes made while coding:**
- Kite REST uses **our own async httpx client** (not the synchronous SDK); the SDK stays only as a test reference (decoder equivalence test).
- `.gitignore` ignores only the top-level `/data/` (it had hidden `tests/data/`).
- CI uses actions/checkout@v5 and setup-python@v6 (Node 20 deprecation).
- Decoder measured at ~17 µs per full packet (≈1 % of a CPU at 750 ticks/s).
- Paper broker is conservative on purpose: fills at shown depth only, queue position from depth, volume counts only after we join, no price improvement for resting orders, triggered stops arrive as new orders. Shared rules (market protection, 25-modify cap) live in `broker/rules.py` so paper can't be more lenient than Kite.
- K3 is **one PR per issue** (stacked), not one per milestone: smaller reviews.
- Throttle uses **sliding windows**, not token buckets (a bucket of 8/s can let 15 out in one second). Cancels get 10/s reserved room; manual clicks are refused with "try again in X s" (rounded up); algos wait in order.
- Error meaning: BrokerError = clear refusal; any other exception = **unknown outcome** → UNCONFIRMED → reconcile. Kite's NetworkException/DataException/GeneralException/5xx/timeouts are unknown. Places are never retried automatically.
- Event log uses Python's built-in **sqlite3** (WAL, writer thread), not SQLAlchemy/Postgres yet: no dependency, fast enough; Postgres later = one new class.
- Money shown in lakh/crore grouping (`format_rupees`: ₹56,27,250.00).
- Positions: weighted average cost with **exact fractions** (no paise leak); applied by cumulative filled qty (replays never double count); carried positions seeded at the previous close (today's P&L starts at 0). P&L is gross (charges estimator later).
- Auto kill switch on the daily loss (`DailyLossWatch`), once per day so a reset lets you close.
- Position reconcile alerts only after **two checks in a row** (re-check in 10 s), so in-flight fills don't cause false alarms; never auto-corrects.
- Server security: Argon2id (not bcrypt) + TOTP once-only codes; lockout 5/15 min; per-IP limit; __Host- cookie (HttpOnly, Secure, SameSite=Strict), only the token's hash stored; Origin check + JSON-only for changes; security headers; no /docs. User/accounts/sessions in SQLite (`server/users.py`); TOTP secrets encrypted with `MOCKTRADE_SECRET_KEY`.
- `platform.py` wires each account (risk → throttle → manager → book → kill/loss watch → reconciler → event log).
- Stream: prices/positions conflated (250 ms, newest wins), orders never; slow browser closed with 4408 (never silent gaps); reconnect = hello → snapshots → merge by updated_at.
- Mutation testing now uses `scratchpad/mutate.py`-style harness that always restores files (a killed run once left a mutated line; the suite caught it).
- Ladder (K5.4): PIQ is estimated in the browser with the paper broker's own queue rule (Kite doesn't report it), so paper and live agree; VAP is counted on the server from every tick (`marketdata/vap.py`, `GET /api/instruments/{id}/vap`) and continued from the stream. The browser makes client order ids (a lost "place" answer can't double-place). Cancels are allowed while trading is stopped (`cancelBlock` vs `tradingBlock` in `ui/src/state/gate.ts`). Optional confirm-over (Cfm) per ladder.
- K5.5: `GET /api/accounts/{a}/events` = the account's audit trail newest first (`EventLog.page`), including its kill switch (`account <id>`) and the firm's. Audit exec types: SUBMITTED=Pending new, SENT=New, FILL=Fill/Partial fill, UPDATED=the new state (Working/Partial/Filled/Cancelled/Rejected), CANCEL_SENT=Pending cancel, MODIFY_SENT=Pending replace, KILL_SWITCH=STOP/Resume. Live audit catches up from the log 300 ms and 2 s after each order event and every 5 s (the log writes in a background thread). Order book and positions "drive" the link group (click = linked ladder switches). Grids scroll sideways in narrow windows. CSV export guards against spreadsheet formulas.
- **Order spec (#175): backend provides, frontend displays.** `GET /api/accounts/{a}/instruments/{id}/order-spec` (`domain/order_spec.py`) lists the broker's order types by what they need (price/trigger: required|none, market protection: auto|none), validities, named rules (`stop_trigger`, `stop_limit`), `priced_by_click`, plus products, tick, lot, auto protection %. Broker adapters publish `order_spec()` (Kite and paper share `broker/rules.order_spec`); the order manager enforces it. Ticket and ladder draw from it (`useOrderSpec`), nothing hard-coded. Apply the same pattern to everything new (algo parameters, products, limits, permissions).
- **Pop-outs (#176):** a widget's window loads `/popout.html` and is drawn by the main page (dockview `addPopoutGroup`), so it shares the one WebSocket/store/link groups: zero sync delay. `/popout.html` has its own CSP (inline styles yes, scripts never). Desktop app = `ui/desktop/` (Electron 44, opens the server, https only; `src/security.ts` rules tested). Electron's binary download fails through Node here: fetch the zip with curl and unpack into `node_modules/electron/dist` + `path.txt` (see how it was done in the #176 session); CI skips the binary (`ELECTRON_SKIP_BINARY_DOWNLOAD=1`).
- **Speed (Ayush, 14 Oct): "ensure syncing and latency are very fast from our side".** Measure every hop (K7.3) and remove waits we add: first change goes out at once, conflation only during bursts.
- **Algo engine (K6.1):** `algos/engine.py` + `algos/spec.py`, design `docs/design/algos.md`. Children marked by `parent_id` (`OrderRequest.from_algo`); `algo_id` = exchange-issued id only (none yet). In-flight children count against the parent; a parent isn't final while one is in flight. `AccountRuntime.algos`; `Platform.add_account(algo_types=...)`.
- Stream (#178): wait for a change → send at once if nothing went out in the last 250 ms, else wait the rest and send the newest. Measured 1–6 ms tick → browser (was ~200 ms).
- Paper refuses stops whose trigger the market already passed (Kite does too); SL trigger vs limit checked in `OrderRequest.validate`.
- Ticket "confirm" is derived from the exact order (`armedFor === signature`), not reset by an effect: an effect-based reset caused a timing-dependent test failure.
- Lesson (K5.3): before pushing, check `git status --ignored` for source files hidden by `.gitignore`.
- Risk: reducing a position is always allowed (even past limits or the daily loss); worst-case position counts working orders; option selling to open is off by default.

**UI direction (Ayush, 14 Oct, after the first mockups):** the first mockups looked like an AI-designed website. The UI must look and work like a professional trading terminal: dense multi-window workspace, many small click-to-trade ladders (Work | Bids | Price | Asks | LTQ | VAP, qty preset buttons, L/M and TIF on the side; click bid = buy, ask = sell, Work = cancel), compact grids (orders, positions, audit trail), tiny window chrome, 11–12 px type. Ladders follow common professional patterns; never copy or name any other vendor's product (Ayush plans to deploy MockTrade commercially in India later). Very convenient for the user above all.

**UI plan (promised 14 Oct):** before any UI code, a UI design pass with mockups for approval: dark-first design system, workspace model (dock/tab/pop-out/linked widgets), every screen (ladder, ticket, blotter, positions, audit, algo manager, admin, morning login), interaction and safety rules.

## Next
1. Ayush merges in order: #123 → #157 → #158 → … → #176 → #178.
2. K6.2 slicer (#149) → K6.3 spreader (#150) → K6.4 algo manager screen (#151). Strategies subclass `BaseStrategy` and act only through `AlgoContext`; each publishes an `AlgoSpec` (see `docs/design/algos.md`); register them as `AlgoType`s in the demo/platform wiring.
3. K7 deploy (#152–#155): Docker + Caddy → Mumbai server checklist → latency/load → morning routine + runbooks. AWS account, static IP and Kite IP whitelist are Ayush's steps.
4. Then the roadmap in `docs/design/product-vision.md` (K8 options, K9 synthetic orders, K10 accounts & charges, K11 replay/backtest, K12 charts & alerts, K13 admin & phone): create each milestone's issues before coding.
5. Ayush, when ready: create the Kite Connect app (₹500/month data plan) at developers.kite.trade and run `python -m mocktrade.tools.login`; build the desktop installer on Windows.

## Key facts the design depends on (checked 14 Oct 2026, re-check before going live)
- Data ₹500/month per app; order APIs free.
- About 1 tick per second per instrument (sometimes 2); full mode 184 bytes with 5 bid + 5 ask depth; big-endian; prices in paise.
- 3000 instruments per WebSocket, 3 connections per API key.
- Orders 10/s, 400/min, 5000/day per user; 25 modifications per order; quote API 1/s.
- Access token expires 6 AM next day; manual login daily.
- Static IP required for order endpoints only (1 primary + 1 backup), set in developer console → IP Whitelist.
- MARKET and SL-M orders need market_protection −1 or 1–100 (0 rejected).
- SEBI: ≤ 10 orders/s per segment without exchange registration; personal use only; no open APIs.

## Paused: COMEX/MockCME track
Old milestones Phase 0–8 and v0.1–v1.0 stay open but their 95 open issues carry the label `paused: cme-track`. The build map (`context/build-map/`) describes that track.

## Tool limits seen in Claude's GitHub access
- GraphQL blocked: no Projects boards or issue transfers from Claude; Ayush does those.
- Repo settings and new repos: Ayush does those.
