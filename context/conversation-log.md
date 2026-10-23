# Conversation log: how MockTrade and MockCME came to be

A summary of the first long planning conversation (5–12 Oct 2026), in order. It replaces the raw chat, which can't be committed because it contains the firm's internal details.

## 1. Understanding a professional trading platform (private)
- Ayush wanted to understand the trading platform his firm uses: its architecture, latency and how orders travel. He shared drawings and a few config and UI files.
- Claude explained it in very simple language with flowcharts and produced three private reference files: **NG One Chart**, **NG Architecture** and **How NG Works**. They stay private, outside the repos.
- Key ideas he took away, which MockTrade copies in generic form:
  - Robots and the risk guard sit **next to the exchange** (co-location), because Jaipur → Chicago is about 85 ms one way, slower than any robot.
  - A central **hub** handles screens and logins. Screens never talk to the exchange directly.
  - There's a **fast lane** for orders and prices, and a **careful lane** for fills, positions, P&L and audit.
  - Every order passes a **guard** (risk checks) and then a **runner**, which speaks the exchange's language.

## 2. Deciding to build his own: MockTrade
- He decided to design and build his own platform from scratch as a learning project. He named it **MockTrade** (not NG) and runs it against a mock exchange replaying real data.
- Goal: a real, complete project he understands bit by bit, with the whole one-chart architecture built in mock form.
- He asked Claude to act as the expert team and to put the whole plan into **GitHub issues**, so he can go step by step.

## 3. Repo and backlog (MockTrade)
- Repo `ayushsharma05/MockTrade` (private). Copilot made the folder structure, and Claude added CI (`pytest` on PRs).
- Claude created labels, 18 milestones (Phase 0–8 design, then releases v0.1–v1.0) and issues #3–#121. #119 tracks that every box of the reference architecture gets built.
- Ayush finished the Phase 0 setup on his Windows laptop (Git, Python, VS Code, his first PR).
- Phase 1 (the trader): user stories and a trader interview (he is the trader). He felt discovery was taking too long, so it was compressed into a one-page **product brief** (`docs/requirements/brief.md`, PR #122): scope GC/SI/HG, multi-leg structures, GTC orders, hard latency numbers and 15 core stories.

## 4. Studying CME (Phase 3)
- #14: a paper exercise on how matching works (sells 2400.4 × 3 and 2400.5 × 5, then buy 6 @ 2400.5).
- He asked why study CME's MDP at all, since MockTrade isn't tied to one exchange. Answer: the **adapter pattern**. Generic ideas go in the core, CME-specific names and numbers go only in the adapter. CME's specs are excellent and public, and his data is in CME format.
- He asked what to read on the CME Client Systems wiki. The pages load with JavaScript and couldn't be fetched, so Claude wrote a beginner guide from CME's public specs: [cme-wiki-guide.md](cme-wiki-guide.md), with 3 diagrams.
- He closed #14, #15 and #16.

## 5. The build map
- He asked to see which issue programs which part of the architecture, so he always knows where he is.
- Claude built the **MockTrade Build Map**, an interactive page: every box of the architecture with the issues that design it (dashed) and build it (coloured by release), a "you are here" marker and a release slider. Files are in [build-map/](build-map/); screenshot below.

![MockTrade build map](images/mocktrade-build-map.png)

## 6. The exchange gets its own repo: MockCME
- He suggested building the mock exchange first, in its own repo, because a real exchange exists before the platforms that connect to it.
- Agreed, with one change: write the **spec in full**, then **build in releases** (E0–E8), rather than "complete" all at once.
- Ayush created `ayushsharma05/MockCME`. Claude pushed the skeleton (package folders, smoke test, CI, PR template), created 9 milestones and 40 issues written code-first (goal → steps with file and function names → tests to write first), and opened design **PR #41** (`docs/design.md`).
- The 6 exchange issues in MockTrade (#34, #37, #38, #49, #52, #73) were closed as moved, with links. #39, #50, #55, #76 and #77 got cross-links. The build map now points to MockCME's milestones.

![MockCME architecture](images/mockcme-architecture.png)

## 7. Mid-conversation questions worth remembering
- **"Are we writing our own TCP and UDP?"** No. The operating system provides TCP and UDP through sockets. We write the protocols on top: iLink-lite for orders (logon, sequence numbers, heartbeats, resend) and MDP-lite for prices (numbered packets, gaps, snapshots). E3 builds reliability on top of UDP, the same core idea TCP uses. A "write a tiny TCP yourself" side-quest issue was offered for later.
- **"Show the diagram here."** The MockCME architecture above, with its order path explained.

## 9. Change of plan: NSE/BSE on Kite Connect (14 Oct 2026)
- Ayush paused MockCME and asked for MockTrade to connect to the real Indian market through Zerodha Kite Connect, designed like a professional platform team, with a broker adapter (real Kite + paper simulator), honest limits, and a one-small-server deployment ($100 cloud credits, ₹500–800/month budget).
- Claude researched Kite and SEBI rules (sources in the design doc). Key findings: ~1 tick/s per instrument, 5-level depth, 10 orders/s, static IP for order endpoints since April 2026, market_protection mandatory, daily manual login.
- Decisions: one Python asyncio process (modular monolith); official SDK for REST, own asyncio WebSocket client + decoder; SQLite event log; FastAPI; React + TypeScript widgets; AWS Lightsail Mumbai 2 GB ($12/month, static IP included).
- 33 issues created (#124–#156), old issues labelled `paused: cme-track`.
- Mid-way Ayush asked to keep the desktop app and widget forms in mind (like Electron desktop trading apps): the UI became a dockable widget workspace (#145) with an Electron shell later (#156, Tauri as the lighter option to compare).
- He also asked for progress updates as work happens, in case usage limits run out mid-task.

![Kite overview](images/kite-overview.png)

## 10. Multi-user, broker-agnostic, and Claude codes it (14 Oct 2026)
- Ayush asked for MockTrade to be **multi-user** (users registered through their broker's login), following all exchange rules, with Zerodha as only one broker (others or own exchange access later), modelled on the reference platform's professional architecture.
- Research found: a retail Kite app is bound to one client id (multi-user needs a Zerodha arrangement); Kite data can't be shown to other users (needs an authorised data vendor); serving others makes the platform an algo provider (exchange empanelment; broker is principal; algo IDs); black-box algos need SEBI RA registration. Design runs for him + family accounts until those gates are passed.
- Delivered the **MockTrade One Chart** (`context/one-chart/one-chart.html`, PNG in images) and §0 of the design doc.
- He asked Claude to **write the whole project** professionally with extensive tests, sending progress updates; he'll type "continue" when limits reset. K0 (#158) and K1 (#159) done, CI green.
- He asked that the UI be designed as thoughtfully as the backend (as with a chart-trading app he works on); a UI design pass with mockups is planned before UI code.

## 11. K2 paper broker (14 Oct 2026)
- Ayush asked that no part be rushed: "treat every part as one project and brainstorm and focus until it finishes". Claude adopted brainstorm → design → code → extensive tests → self-review → PR + context for every part.
- The self-review before tests found 5 realism bugs (double-counted volume, price improvement for resting orders, qty raise keeping queue place, a stale OPEN after cancel, string-id ordering). Writing the tests found 4 more (no volume baseline on the first tick, trade-through only partly filling, triggered stops filling at their own price, volume from before we joined counting). All fixed.
- Each fill rule was then broken on purpose to prove a test catches it (10/10 caught). PR #160, 167 tests.

## 12. K3 orders and risk (14 Oct 2026)
- After lunch Ayush asked Claude to continue on its own (limits reset at 12:10 his time), "treat every point of this project as a separate project", and make the app "very useful, intuitive, scalable and reliable".
- Claude built K3 as five stacked PRs, each brainstormed first (the real-world failure cases in a table), then code, tests, a mutation check (breaking each rule to prove a test catches it) and self-review:
  - #161 order manager: forward-only states, match by tag then broker id, idempotent submit, UNCONFIRMED on unknown outcomes, cancel before ack.
  - #162 throttle: sliding windows instead of the issue's token bucket (a bucket lets 15 orders out in one second); tests caught algo orders jumping the queue.
  - #163 risk checks and kill switch: 11 checks with trader-readable reasons; closing is always allowed; STOP engages first, then cancels.
  - #164 event log: SQLite writer thread, retries without loss, restart = rebuild + reconcile + continue ids.
  - #165 Kite adapter: which Kite errors mean "not placed" vs "unknown"; order fields checked against the official SDK; reconcile on every reconnect; amo_check tool for the go-live check.
- Diagram: ![K3 orders and risk](images/k3-orders-and-risk.png)

## 13. K4 positions and reconciliation (14 Oct 2026)
- Ayush said "continue". Claude built K4 as two stacked PRs, same process (brainstorm table of traps → code → tests → mutation check → PR):
  - #166 positions and P&L: exact fractions for averages like ₹100.075; realised + unrealised checked against the cash-flow formula on random fills; replays never double count; auto kill switch on the daily loss, once per day.
  - #167 reconcile with the broker's positions: alert only after two checks in a row (a fill can be in flight), quick 10 s re-check, never auto-corrects, outside trades flagged.

## 14. K5.1 server and K5.2 stream (14 Oct 2026)
- #168 server: threat table first (passwords, code replay, brute force, cookies, CSRF, impersonation, fake broker callbacks, wrong Zerodha account, leaked DB). Tests found that failed-login counts were rolled back by the database, so the lockout never worked; fixed. A mutation run killed by a timeout left one line mutated; the suite caught it, and the mutation harness now always restores files.
- #169 stream: prices conflated, orders never; slow browsers disconnected rather than silently skipped; contract written in docs/design/stream.md. Tested against a real uvicorn server.
- Next: the UI design pass with mockups for Ayush's approval (promised earlier), before UI code.

## 15. UI design pass: direction changed (14 Oct 2026)
- Claude drew 7 mockups (design system, workspace, ticket states, stopped, login, audit, phone) on a design canvas.
- Ayush: "looks more like an AI designed website, instead it should look like a trading platform"; trading must be very convenient; common professional ladder designs are the reference, never copied. He shared screenshots of his firm's platform for reference only (not saved anywhere, never to be committed).
- Claude rebuilt the ladder as a working, clickable prototype (simulated prices and fills) and the workspace as a dense terminal (5 ladders, market watch, orders, positions, audit trail grids). Remaining boards are marked "old style" until the direction is approved.

## 16. K5.3 skeleton and K5.4 ladder, continued in a cloud session (14 Oct 2026)
- The claude.ai chat ran out of usage right after opening #170 (K5.3). Ayush continued in a Claude Code cloud session (with $100 credit), asking it to read the whole repo and context first and to remember every point from the chat.
- #170's `ui` CI was red: `ui/src/lib/format.ts` had never reached GitHub because the template Python `.gitignore` ignores every `lib/` folder. Rebuilt from its callers and tests (Indian grouping, real minus sign, India-time clock); `.gitignore` now ignores only `/lib/`. CI green. Ayush OK'd pushing straight to the milestone branches (k5-3-ui, k5-4-ladder) as before.
- K5.4 ladder (#171), built to the approved canvas board 0: Work | PIQ | Bids | Price | Asks | LTQ | VAP, sidebar Acct/Type/TIF/Prod/Cfm, lots presets, AUTO centre, CXL ALL. Brainstormed traps first (tick grid, 5 levels only, PIQ after my order shows, lost "place" answer, unconfirmed orders, big mis-clicks, market protection, getting out while stopped, rows jumping). PIQ is estimated in the browser with the paper broker's rule; VAP counted on the server. 13/13 mutations caught; paper trading checked end to end in a real browser.

## 17. K5.5 screens, and keeping context current (14 Oct 2026)
- Ayush said "continue". K5.5 (#172): Order Book, Positions, Audit Trail (with each order's timeline, live catch-up, CSV) and Messages, built to canvas board 2; a new server endpoint pages the account's audit trail. Same process: traps table, tests, 13/13 mutations, real-browser check. The browser check found two things fixed before the PR: fills showed as two identical "Filled" rows (now "Fill" for the trade, "Filled" for the state), and narrow windows squeezed columns (now they scroll sideways).
- Ayush asked that context be updated at every important point, because each chat hits its limit (1M tokens here) and the next chat must continue from "continue" alone. Added a "How to continue" checklist to `context/README.md`, the check scripts in `context/scripts/`, and a continuity section in `working-with-ayush.md`.

## 18. "Complete the whole project" and the product direction (14 Oct 2026)
- Ayush asked to complete the whole project, designed like the platform a top professional trading-software company would build for India, paying only for the Zerodha API. Every component independent and replaceable. Claude acts as the whole expert team.
- He then said: don't copy or mention any other vendor; MockTrade is its own product and may be deployed professionally in India later. All vendor mentions were removed from the repo and context.
- And: "backend provides, frontend displays". The UI must not hard-code order types or other market rules: the server publishes an order spec (types, required fields, validities, products, rules) and the screens render from it. Applied first to the order ticket and ladder (#173).
- Plan agreed: order ticket (#173) → Electron (#156) → K6 algos (#148–#151) → K7 deploy (#152–#155), one stacked PR each, context updated at every PR; then a product-vision catalogue of what a professional Indian-market platform provides, turned into new milestones.

## 19. Order spec, desktop app and speed (14 Oct 2026)
- #175: order ticket for every order type, drawn from the server's **order spec** (Ayush: "frontend displays what the backend provides; another market may have 5 order types"). The spec is enforced by the order manager too. Paper now refuses stops the market already passed, like Kite.
- #176: pop-out widget windows (browser and desktop) that share the main page's live connection, and the Electron desktop app with locked-down pages, measured in a real Electron run.
- Ayush: "ensure syncing and latency are very fast from our side". Pop-outs already add no sync delay; next is the stream (send at once, conflate only bursts) and K7.3 measurements.
- #178: the stream sends a change at once when nothing went out in the last 250 ms, so a single change arrives in 1–6 ms instead of about 200 ms. Order and algo messages are never conflated.
- K6 algos: #179 engine, #180 slicer, #181 spreader (hedges on every update, never chases beyond the pay-up), #182 algo screen. The algo start form is drawn from each algo's spec, the same "backend provides, frontend displays" rule as the order spec. Started both algos from a real browser against the demo server: each ran to Done, and the spread hedge went out 86 ms after the first leg filled.

## 20. K7 starts: a real server entry point and Docker (14 Oct 2026)
- There was no production entry point, only the demo. #184 adds `tools/serve.py`: settings from the environment, paper accounts on simulated prices, and kite accounts listed as not running until #183 (opened as a new issue: live Kite wiring).
- Found and fixed: uvicorn 0.53 re-raises SIGTERM after shutdown, which killed the process before the audit trail was flushed. Serve now exits 0 after a clean shutdown, and a test sends a real SIGTERM.
- Docker was checked for real in the cloud session. `dockerd` runs there. Docker Hub returned 429, so images came from `mirror.gcr.io` and were tagged with their standard names. The sandbox's TLS proxy CA was added to the local base images only; the committed Dockerfile is clean. Script: `context/scripts/e2e-compose.cjs` (log in through Caddy and trade).
- #185 (#183): live Kite in the server. Kite accounts are known from boot (orders refused until the daily login). "Log in to Kite" on the account strip goes live with no restart. The instrument list downloads once a day and is cached. A new `MarketData.on_demand` hook subscribes a contract the moment a screen opens it (speed). Exactly one ticker carries prices, so VAP isn't doubled. 6 AM and refused connections log the account out. A real-account login is Ayush's manual step (#155 runbook).
- #186 (#153): `tools/backup.py` (SQLite online backup, owner-only), `deploy/server-setup.sh` (Ubuntu hardening), `docs/runbooks/server.md` (Ayush's AWS, DNS and Kite steps, AMO IP check, restore). Ayush closes #153 after the IP check.
- #187 (#154): measure first. A two-process load test with a fake Kite showed 62 µs of Python work per tick and ~100 ms GC pauses. Fixes: NamedTuples, one struct unpack per packet, orjson, `gc.freeze`, and chunked decoding that hands a frame on whole. Result: 21 µs per tick and 10–20 ms GC pauses. Kite → screen p50 65 ms at 1,000 contracts × 2 ticks/s, on a noisy VM. Re-measure on the Mumbai server.
- #188 (#155): `ops/` (scheduler in India time on trading days, alerts with dedupe and an optional phone webhook, checks: the Kite login reminder escalating at 09:05, prices stopped, disk). The screens show ops bands. Runbooks: morning routine and incidents. K7 code complete; Ayush's AWS and real-day steps remain.

## 21. K8 Options (14 Oct 2026)
- #194 pricing (Black-76 off the future, IV with a safeguarded Newton, Greeks in traders' units; 16/16 mutations).
- #195 chain service: REST plus a throttled stream; the simulator prices options with a smile.
- #196 chain widget: mirrored columns, ATM, OI bars; double-click a tab to maximize.
- #197 strategy builder: server templates, exact breakevens, "unlimited" detection, calendars; Alt-click legs from the chain.
- #198 baskets: one cumulative check, buys first, stop on refusal, broker margin port. A new **covered-short** rule: spreads are allowed while naked shorts stay refused.
- Each was checked in a real browser against the demo. Ayush repeated (mid-session): a professional Indian-market platform, no references to other vendors, replaceable parts, the backend provides and the frontend displays, speed everywhere. All already in `working-with-ayush.md`.


## 22. Recovering the stacked merges (15 Oct 2026, India time)
- Ayush asked this assistant to read the saved context, audit the randomly merged PRs, and bring main to the point where Claude Code stopped.
- The code was preserved: #174 brought K0–K5.3 to main; #203 merged into k8-5-baskets and #198 then merged into k8-4-strategy. Their commits include all subsequent code through K9.1. The newer context remained on kite-architecture.
- Recovery combines those histories in one integration PR, retaining the original commits and a backup of pre-recovery main. No code revert is required by the merge audit.
- Main CI had 450 lint errors in the historical context generators, before Python tests ran. These archived, session-specific utilities are now explicitly outside application lint scope; all src/ and tests/ rules remain enabled.
- The intended stopping point is K9.1 complete. #200 synthetic orders, #201 GTT, and #202 screens remain pending. AWS deployment, AMO checks and a real trading day remain manual work.
- Future code and context updates travel in the same PR against main, avoiding a second divergent context branch. See merge-recovery-2026-10-15.md for the audit and validation.

## 23. Back after the recovery (15 Oct 2026)
- Ayush: after Claude got stuck he merged PRs out of order, asked another assistant to fix the merges, and it made some changes; "recheck and continue".
- Checked: main holds Claude's K9.1 code byte for byte (no src/tests/ui/deploy change); the extra changes are docs and context from the old context branch, a Ruff exclude for historical context scripts, and the rule "code and context in the same PR against main". CI on main is green; no PRs open; #199 closed, #200–#202 open. Followed the new rule from here.
- Two new issues from that review are real safety gaps, so they go before K9.2: **#207** a working buy counted as cover, and a basket sent its shorts as soon as the buys were accepted; **#206** production start-up never restores orders from the event log.
- #207 fixed: only filled longs cover; every option sell checked (also selling the long that protects a short); modify that grows a sell re-checked; baskets wait (events, 10 s bound) for the buys to fill, else cancel them and send no short.
- #206 fixed: each account starts with a recovery gate (new orders and modifies refused, cancels allowed). Once the broker answers (Kite: after login) it rebuilds carried positions (Kite overnight qty at the close; paper from earlier days' fills at the close), replays today's fills once each, restores today's orders, reconciles with the broker's order book, closes paper orders the paper broker forgot, and compares positions. Any difference keeps trading shut with an ERROR alert; a person settles it and restarts. Algo leftovers are cancelled and position checks start only after recovery. Checked with a real demo restart.
- Ayush: "merge yourself every pull request", and asked how much work is left. Claude merged #208 and #209 on green CI (fixed a random "broken pipe" in `deploy/smoke.sh` along the way: `docker run | head -1` with pipefail). Estimate given: about 18 issues left (K9.3–K9.4, K10 accounts & reports, K11 research, K12 charts & alerts, K13 admin & phone), plus Ayush's own deployment and compliance steps.
- K9.2 done: trailing stop, OCO exit and bracket as `protection` algos (`algos/protect.py`); the engine's main leg is instrument + side; protection running at a restart is named in an ERROR alert once. Lesson: never run two mutation scripts at once or kill one mid-run (it left a mutant in the source); the script now restores in `finally` and treats a hang as caught.
- K9.3 GTT: `GttBook` broker port (Kite `/gtt/triggers`, paper simulation that places the order from the broker like Kite), `GttService` (spec checks, risk checked at each trigger price, audit, 5 s polling), and `OrderManager.adopt` so a fired GTT's order is ours (fills reach positions; no false mismatch). Found and fixed a paper-broker `settle()` spin (gathering finished tasks never yields). Lesson repeated: `pkill -f "<pattern>"` kills the calling shell when the pattern is in its own command line: anchor with `^`.
- K9.4 screens: GTT Book widget and form drawn from the GTT spec (which now also publishes legs and labels per kind), Order | Algo | GTT tabs in the ticket reusing the same forms (`StartForm`, `GttForm`), algo picker grouped by family, "held" tag on protection algos. Fixed a UI race found by a test: the form's start-from-market effect could overwrite a trigger typed in the same moment (now decided on the newest state).
- Ayush (late 15 Oct): "75% context is used; keep context in the repo so another chat can do exactly the same; don't drop quality." Handoff written in `status.md` § Next (where we are, K10 plan with issue list, how each piece is built, session lessons).
- Ayush (15 Oct, night): usage at 82%; keep working until 97%, and give a prompt for a new chat that carries on exactly, with the same quality. Claude gave the prompt (read `context/README.md` and everything it points to, check out the branch in progress, follow `status.md` § Next) and pushed work in progress after every step so a new chat can take over at any point.
- K10.1 (#213) funds, holdings, tradebook: optional broker ports; Kite's figures shown as given (never re-added); paper money as an explicit estimate (cash model + plain margin rates, worst case of working orders, holdings sold as a credit); paper CNC from earlier days becomes holdings at a restart (as at Kite); a service that never empties a screen on a failed read; Funds and Holdings widgets and a Trades tab. Checked in a real browser on the demo.
- Ayush (16 Oct): hit his usage limit mid-work; it reset: "continue from where you left off". Claude merged #218 (K10.1) on green CI and carried on with K10.2.
- K10.2 (#214) charges: rates checked on the web (Budget 2026 raised STT on F&O from 1 April 2026: futures 0.05% of the sell value, options 0.15% of the sell premium); sources disagree on exchange/IPFT rates by a few rupees a crore, so the table is data with a "checked on" date and a JSON override, and the docs say to check before real money. Hand-worked tests per segment caught two real bugs before any screen did: F&O instruments (NFO/BFO) got no exchange charges because the fee table is keyed by NSE/BSE, and rounding per instrument drifted a paisa from the total. Positions show gross, charges and net; the ticket shows an estimate before sending.
- K10.3 (#215) reports: built from the audit trail with the live screen's convention (carried in at the previous close), so days add up (checked on two hand-worked days). Past prices aren't stored, so the 16:00 IST job keeps each day's report with closing prices; a day rebuilt later says it has no prices instead of guessing. PDF from a small built-in writer (no new dependency), read back with a PDF library. The browser check found a real bug: today's preview didn't refresh after new fills (the tab had loaded earlier); it now re-reads after every fill.
- K10.4 (#216) margin check: a separate `MarginGate` (recovery → risk → margin → throttle) that never asks about exits and fails open with an audit note when the broker or funds can't answer (a margin outage must never block getting out). Uses the broker's `final` margin (positions considered). Paper quotes margin from `PaperMargins`. The browser check also showed the existing ₹50 lakh per-order value limit and the 1 s double-click guard working as designed (they stopped bigger or repeated test orders first).
- K10.5 (#217) MTM limits and square-off: closing always goes through the normal order path (risk, margin, throttle, audit), split by the per-order lot limit under one basket id; risk now lets identical legs of one basket through (otherwise a split close looked like a double click). Rules are alert-only unless auto-close is ticked, fire once a day, and are kept as JSON so a restart doesn't drop a stop. MIS cut-offs are data (equity 15:20, F&O 15:25 IST; verify before real money). The mutation script showed a trap in mutation scripts themselves: a short search string can match inside a more indented line earlier in the file; anchors now include the next line.
- K10 complete (PR #222 merged). K11 issues created: #223–#226. K11.1 tick recorder: a `MarketData.tap` (one append on the trading loop), a worker-thread flush every second as one gzip member per flush (crash loses ≤ 1 s; a cut member is skipped on read), exact microsecond times, rotation by days kept. Measured on the demo: 86.6 bytes a tick with full depth (≈ 2 MB per instrument-day), read back at ~43k ticks/s.
- K11.1 merged (#227). K11.2 (#224) replay: a separate replay server (`demo --replay <day>`, own data folder) so practice can't mix with live; every component gets the replayed clock. Tests and the browser check found three real issues: pause took effect one tick late, a jump waited out the gap at its target tick, and at full speed the paper broker skipped ticks (a subscription keeps only the newest) and an order's acceptance hadn't run before the next tick; fixed with the broker's every-tick mode and three loop turns between ticks, so a replayed day gives the same fills as live at any speed.
- K11.2 merged (#228). K11.3 (#225) backtests: one fresh platform per day with the real engine, paper broker, risk and charges, driven tick by tick (no wall clock): the engine's heartbeat on replayed time, and a drain between ticks until the broker and engine are idle, because the first try stamped fills one tick late (an order's path takes about a dozen loop turns). Same inputs, byte-identical reports. Candles become ticks adverse-path-first with a tick of spread (conservative, labelled).
- K11.3 merged (#229). K11.4 (#226) research screens: Recordings and Backtests widgets, everything from the server (a new form endpoint publishes the algos with their settings, recorded days, products, defaults, candle days; an unknown algo is now refused before a run starts). Runs are polled only while one is going; a picked run is re-read as its progress moves. Compare keeps each run's colour while others are ticked or unticked (colour follows the run). The chart is plain SVG; its four colours were run through a colour-blind check on the dark background. The mutation script first left 3 survivors: a CSV test that a PDF would also pass (now checks the CSV header), a capital check that could never fire (removed: the price parser never returns 0), and the "re-read the picked run" rule (new test). The browser check found two layout faults (single-day dots stretched into ovals, clipped columns), both fixed.
- K11.4 merged (#230). Re-reading #226 showed two parts not built (delete a recorded day; start a replay from the screen), so they became #235 (K11.5) instead of being dropped silently.
  - Decision: the screen shows each day's replay command (Copy) rather than the server starting replay processes; that belongs with the K13 admin panel. Found and noted a real gap: a live server's recordings can't be replayed yet (the replay tool knows only the demo's instruments).
  - The mutation script caught an editing slip of Claude's: the store interface (Protocol) had been given a method body.
- K12 issues created: #231–#234.
- K11.5 merged (#236). K12.1 (#231) candles:
  - **Design:** only 1-minute candles are built live (cheap per tick); bigger ones are added up on request. Buckets sit on the session grid in India time. Volume comes from the day's total, as VAP does; quotes are ignored.
  - **History joined to live:** each bucket appears once, and the builder's partial first minute never replaces a whole one from history.
  - **History sources:** Kite's historical API is in the ₹500 plan (checked on the web). Its range limits and 3/s rate come from the forum only and are flagged to verify. On paper and the demo, history comes from the recordings.
  - **Fixes:** self-review caught a performance trap: today's recording was re-read on every request. It is now rebuilt at most once a minute. The mutation script found two gaps in the tests (quotes in a new minute; the per-connection chart limit), now covered.
  - **Check:** on the demo, a minute from the API matched the same minute worked by hand from the recording.
- K12.1 merged (#237). K12.2 (#232) chart widget:
  - **Built:** canvas candles with a volume pane, the live forming candle, the account's fills as ▲▼ marks, and older pages as you pan.
  - **Click-to-trade:** a click picks a price for the linked ticket over the link bus. A pick never sends an order; the ticket's confirm still applies.
  - **Colours:** green/red failed the colour-blind check, so candles are blue/orange with hollow up and filled down.
  - **Found and fixed:**
    - The browser check found three real bugs the unit tests had missed: a crash after a drag, a stale older page landing after an interval switch, and two history reads at once making the chart wait more than 2.4 s.
    - It also found a bug already on main: linked widgets' tab titles didn't follow their instrument.
    - The mutation script caught a test that couldn't fail (a snapped price that was already on the tick).
- K12.2 merged (#238). K12.3 (#233) market alerts:
  - **Kinds and firing:** seven kinds in the algo-spec format. Crossing semantics: armed first, "already true" said instead of firing, cool-down for repeats, disarmed after a restart.
  - **Storage:** per-user JSON, written off the loop.
  - **Webhooks:** signed with HMAC and a timestamp, retried on 5xx/429 only, https to public hosts only (no reaching the server's own network).
  - **Fixes:**
    - A test caught a route-order bug: `DELETE /api/alerts/webhook` was taken as an alert id.
    - Self-review found that user ids aren't restricted, so file names are now hashed.
    - The mutation script's one survivor led to a real gap: alert instruments weren't watched, so Kite would never have streamed a contract no screen showed. The engine now keeps them subscribed.
  - **Check:** an alert fired live on the demo. An outside webhook couldn't be tried from the sandbox; test it once deployed.
- 16 Oct, K12.4 (#234) market screens:
  - **Built:** alerts manager, time & sales (prints worked out on the server from tick volume, the side estimated from the touch before), depth window, movers (ranked on the server; OI change since the first OI seen today).
  - **Found in the browser check:** the OI list showed zeros (now ranked by the biggest move either way, zeros left out), and simulator options showed absurd % changes (their day now starts at the model price).
  - **Safety bug found and fixed (from before K12.2):** a linked ticket that switched contract started at the old contract's price for one render. The hooks now return values only for the contract asked. A test proved the bug and then the fix.
  - Ayush said that while Claude's limits were used up, he had an outside AI tool review the whole code. It changed no code but opened issues #240–#279. **Decided order:** finish K12.4, then K13 (core scope), then the core v1 gate #240, the hardening gate #257 (#258–#277, with #279 security and #278 discussion), and only then the post-v1 features #241 (#242–#255). Each finding is rechecked against the code before work starts.
- 16 Oct, K12.4 merged (#280). K13 issues created: #281–#285. K13.1 (#281) admin API:
  - **Design:**
    - The risk limits are published as a spec (the algo-spec format plus the kinds `money`, `choices` and `instruments`), and the server checks edits against it.
    - An edit applies from the next order everywhere a limit had been copied at the start: the checks, the daily-loss stop, and the MTM and square-off closing pieces. It is saved for a restart.
    - Each change says whether it loosens a limit, and what a tighter one means for open positions and orders. An edit never cancels anything itself.
  - **Rules:**
    - A turned-off account takes only closing trades, from any source (the risk gate), and its traders can still cancel, close and stop.
    - There is always an active admin, and nobody turns themselves off.
    - A reset or new password must be changed before anything else; until then the API and the stream refuse the user.
    - An admin's new paper account runs at once: the platform starts its loops while running.
  - **Checks:**
    - The mutation run found 5 real gaps in the tests, all now covered; 2 of its mutants were equivalent and are left out.
    - On the demo, every step worked, and so did a restart.
- 16 Oct, issue clean-up and the first confirmed defect:
  - **Asked:** Ayush said the phone screens can wait (they don't affect the core design). He asked Claude to check every open and in-progress issue and keep only what is relevant. The repo first existed to understand NG, which the design came from, so NG-derived issues may still matter.
  - **Done:**
    - All ~98 paused-track issues were compared with the code by two helpers and checked by Claude.
    - Each was closed with a note: done on the Kite track, not relevant (COMEX, fake exchange, several sites), or carried on.
    - What NG taught that is still missing became #287–#292: operations; design docs; release process; F&O expiry day; compliance and the SEBI algo id; and a decision for Ayush on user-written strategies.
    - #283 (phone) moved after v1.
    - The outside review's docs PR #256 was folded into the context and closed; its content was notes only.
  - **Decided:** the two defects the review actually reproduced are fixed now, ahead of the hardening phase, because they are small and safety-related.
    - **#269** (candle paths could escape their folder) is fixed: the store checks ids and containment, rows are checked whole and written atomically, imports are audited, and account ids are safe file names. A test showed the bug before the fix.
    - **#258** is next.
- 16 Oct, #258 fixed. Positions are now compared and closed per instrument *and* product, in all of these places:
  - the periodic broker check and the restart rebuild (`differences()` in `positions/reconcile.py`);
  - the paper broker, which now lists one row per product;
  - the "only closing trades" rule, which uses that product's position and its own working orders;
  - the alerts, on the server and the screen.

  The audit's case (NRML +75, MIS −75) no longer raises a false difference. The mutation run found 5 gaps in the tests, all now covered (19/19).
- 16 Oct, Ayush asked about 22 "in progress" issues on the GitHub Project linked to the repo. Claude's GitHub tools can't read Project boards. Ayush was asked to send the list (or a CSV export) so it can be checked against the clean-up.
- 16 Oct, main CI red after #293/#294: Ayush noticed it in the Actions tab.
  - **Cause:** the new admin and candle-import tests read the audit trail before its background writer had written it. They passed on the PRs and lost the race on main.
  - **Fix (#295):** await `log.flush()`. Proven with a deliberately slowed writer: 6 failures before, none after, and the full suite green. Main is green again.
  - **Lesson:** check main after every merge.
- 16 Oct, Ayush's board showed 22 "In Progress" cards. All were closed issues with merged PRs; the project's "closed → Done" workflow is off. Ayush was given the steps to bulk-move them and switch the workflow on.
- 16 Oct, K13.2 (#282) admin screen:
  - **Built:** Users, Accounts and Limits, drawn from the server (roles, brokers, the limits spec); secrets shown once with a QR code; second clicks for dangerous and looser changes; risk read only; the first-login "Set your password" page; Admin offered only to admin and risk.
  - **Ayush restated the workspace design:** widgets open, close and size anywhere; pages are screens; with pop-outs they make one workspace. So the Admin widget was made to adapt to its own width. The rule "every widget works at any size" is now in `docs/design/ui.md`.


## 16 October 2026 — researched later-stage expansion (Codex)

Ayush asked for broad research across seven public trading product ecosystems, customer feedback and detailed later GitHub additions. Reviewed public documentation and available reviews; mapped existing foundations and avoided parallel replacement engines. Created parent #297 and 25 packages #298–#322, then linked them from #241. Each describes purpose, scope, dependencies and acceptance criteria. Coverage and evidence limits are in [feature-expansion-2026-10-16.md](feature-expansion-2026-10-16.md).

Core #240 and hardening #257 remain first, with security #279, data rights #278 and applicable compliance #291 preserved. Mobile new-entry, public marketplace/performance sharing, assistant tools and institutional services are decision-gated. Reviews are qualitative, often dated; paid/private functionality and claimed performance were not independently tested. Requirements remain vendor-neutral. No application code or deployment was changed. Context handoff tracked by #323.
- 16 Oct (after Claude's limit reset):
  - Ayush had ChatGPT survey seven similar products. It opened later-roadmap issues #297–#322 (all after core and hardening; several need decisions) and the docs PR #324. Claude reviewed #324 (vendor-neutral, docs only, the same order of work) and merged it.
  - **K13.4 (#284) keyboard shortcuts done:**
    - one list of actions with scopes; the user's keys saved on the server (a new per-user settings store, reused by themes next);
    - an editor widget that refuses browser keys and clashes in words; STOP's fixed Ctrl+Shift+K;
    - the ticket, ladder and chart follow the user's keys, and the ticket's hint shows them;
    - mutation 27/27; the browser check passed.
  - **K13.5 (#285) themes done:**
    - every colour became a named token (proved identical first), then light and colour-blind safe blocks were added;
    - a test checks WCAG AA contrast for every text colour in all three themes; a few dark colours were deepened to pass;
    - colour-blind safe uses blue for buy, orange for sell and purple for the last price (the first try used blue, which blended with the bids in the browser check);
    - the choice follows the computer until picked, switches with no reload, is saved per user on the server, and the chart repaints;
    - mutation 18/18 (it found two gaps: a redundant `try`, removed, and an untested message clear, now tested); the browser check passed.
- 16 Oct, K13.5 (#326) merged; `main` green. Started the core v1 gate #240:
  - A helper checked each gate item against the code.
    - Already done: restart recovery and its tests, duplicate-fill guards, unknown outcomes, the warning bands, a backup of the two databases, and the day's runbooks.
    - Missing: a real crash drill and an on-screen unprotected warning; a backup of everything and a tested restore; recordings that keep their instruments; a single go-live checklist.
  - Those four became #327–#330 under #240. Live steps stay with Ayush: no live order without his explicit go-ahead.
  - **#327 done:**
    - each recorded day saves `instruments.json` (each instrument described once, before its first ticks);
    - replay builds from it, so an expired, non-demo contract replays and trades on paper with that day's lot size;
    - a day that can't be described is refused before anything starts, naming what is missing;
    - mutation 22/22 (found four test gaps, now covered); the demo check passed;
    - known limit: backtests still use the live list.
- 16 Oct, #327 merged (PR #331). **#328 done:**
  - **A protection guard on the server.** A position that *had* a stop held by MockTrade and lost it (restart, pause, stop, failure, or the stop ending while the position is still open) gets one red UNPROTECTED band. It clears when the position is flat or covered again (a working stop algo, a closing-side SL/SL-M order, or an active GTT).
  - **What changed from before.** The old restart alert existed only in `serve.py`, never cleared, and never appeared in the demo; the guard replaces it in both. It waits for recovery before deciding. Never-protected positions are deliberately left alone, so the band stays meaningful.
  - **The crash drill (SIGKILL, restart twice) passed 17/17:**
    - the position came back at its size, and bought stayed 150, not 300;
    - the resting paper order was closed with a reason;
    - the band appeared and cleared;
    - orders and cancels worked.
  - **The browser check:** the band showed after the restart, and a 9 s freeze showed "PRICES 9 s OLD", which cleared on resume.
  - **What testing found and fixed:**
    - resuming a stop didn't clear its band (a real bug);
    - an early drill counted the broker's tradebook, which is the wrong measure; it now counts our own fills;
    - mutation 27/27, after adding a test for a short position.
- 16 Oct, **#329 done:**
  - **What a backup holds now.** Everything a restored server needs: the two databases plus rules, reports, alerts, candles and backtests. Recordings come only with `--with-ticks`.
  - **What it never holds:** broker tokens, keys, `.env` or temporary files. `manifest.json` gives each file's size and SHA-256.
  - **A new restore command** (`mocktrade.tools.restore`):
    - it checks the manifest, the checksums and SQLite's integrity first, and refuses a tampered or path-escaping manifest;
    - it never overwrites; `--replace` moves the old data aside, and nothing is deleted;
    - at the end it lists what comes from the password manager.
  - **The drill passed 19/19:** a running demo was backed up and restored into an isolated folder. A server started on it had every user, the position, the layout, the theme, the MTM limit, the alert and every audit event. A trade made after the backup was correctly absent.
  - **Mutation run, 24/24.** Along the way it found two pieces of dead code, now removed, and one missing test (a same-size tamper), now added.
- 16 Oct, #329 merged (PR #333). **#330 done:**
  - **`docs/runbooks/go-live.md`** covers six areas: before the day; login and expiry; orders, modifies, cancels and the broker check; market-data interruptions; protective orders; square-off; webhooks. Each step has how to check it, what pass means, paper or test evidence, and a live box.
  - **Paper and live are never mixed:** paper evidence never ticks a live box. The 28 live boxes are Ayush's, with one share and his explicit go-ahead.
  - **Paper run, 19/19** (`golive-paper.py`): limit rests, modify, cancel, refusals in words, market fill, broker check ok, SL-M and GTT placed and removed, square-off asks then closes MIS only, unsafe webhooks refused.
  - **Every test the checklist cites exists and passes.**
  - **A real webhook delivery needs the internet,** so it is a live step.
- 16 Oct, **#288 done** (design docs). Each statement was checked against the code before it was written: `ALLOWED`, `Pending`, the event kinds, the modify and cancel rules, recovery's summary, the kill switch order, the throttle numbers, the cited tests.
  - **New docs:**
    - `docs/design/glossary.md` (about 90 terms);
    - `domain-model.md` (types, fields, the rules they keep);
    - `order-life.md` (the state diagram, the `ALLOWED` table, and partial fill, modify and cancel, rejects, restart, disconnect and STOP as sequence diagrams);
    - `docs/test/README.md` (every test layer, how to run it, the release scenarios);
    - a real `docs/design/README.md` index.
  - **Old stubs:** `docs/adr` now points to where decisions live; `docs/exchange` (CME-era) is removed.
  - **Diagrams:** rendered offline with a local Mermaid (`context/scripts/render-mermaid.cjs`), because the CDN is blocked here. Writing them caught one mistake in my first restart diagram: positions are compared *before* the gate decides, not after.
- 16 Oct, #288 merged (PR #335). **#289 done** (release process):
  - **Versions.** One version (`src/mocktrade/version.py`, `1.0.0-rc.1`) is kept in step with pyproject, `ui/package.json` and the lockfile by a test. The server reports it at `/api/health` with the image's commit, and the status bar shows it.
  - **Notes.** `docs/releases/v1.0.0.md` (scope, migrations, known limits) and `CHANGELOG.md`.
  - **Deploy and rollback.** `deploy/deploy.sh` checks the tag, refuses market hours (`window.sh`; `--emergency "reason"` is logged), backs up, keeps the previous image, builds, and checks health and the version, rolling back by itself on failure. `rollback.sh` (`--restore-data` for migrations). `docs/runbooks/release.md`.
  - **Decided:** the `v1.0.0` tag waits for Ayush's live go-live run and his acceptance on #240. Everything is ready as `1.0.0-rc.1` until then.
  - **The rehearsal** used real Docker in this sandbox (the daemon was started, base images came from Google's mirror of Docker Hub, and a local base image trusts the proxy's CA). It found three real problems:
    1. the old checkout had no deploy script, so the script now runs from the release;
    2. a rollback checks out code without its own script, so it now runs from a copy;
    3. the old image's restore can't work in place on a volume, so restore now moves data aside inside `/data`, and `--restore-data` uses the newer image.
  - **After the fixes:** 15/15. Mutation for backup and restore 26/26. The market-hours rule was first written in Python inside the image; it moved to `window.sh`, because the running image may predate it.
- 16 Oct, #289 merged (PR #336); `main` green. Posted a status table on #240.
  - **Ayush said** he is lost in how much has been built. He wants Claude to finish everything first; then he will start understanding it. He trusts Claude for now.
  - **Decided:** carry on with the hardening gate #257 and everything after it, then write him a simple guide. The steps that only he can do (live trading with his own login, his acceptance, the v1.0.0 tag) wait for that guide, explained slowly.
- 16 Oct (after the limit reset), **#259 done** (unanswered orders).
  - **Two real defects confirmed in the code:**
    - one broker list without an unanswered order rejected it at once, so a slow broker could lead to a duplicate;
    - an unknown Kite status was dropped from the list, which made the order look missing.
  - **The fix:**
    - **Rejecting needs evidence:** 3 lists over at least 60 s, with the times in the reason.
    - **Follow-ups:** the manager asks the broker again by itself, after a restart too.
    - **Unknown statuses** are kept as PENDING with the raw status, and are visible in the audit trail.
    - **A person can settle it:** after checking the broker's book, two clicks on **Not placed?** in the order book, recorded with their name.
  - **Checks:** mutation 14/14, after removing two redundant conditions it exposed and adding a restart test.
- 16 Oct, #259 merged (PR #337). **#260 done** (changes and cancels in doubt).
  - **Defects confirmed:**
    - two overlapping changes could answer out of order and revert the newer one;
    - a change with no answer kept the old terms, so a bigger change's risk was understated;
    - a cancel with no answer cleared its flags, so the order looked normally working;
    - risk checks were not re-run after the throttle made an algo's order wait.
  - **The fix:**
    - **One change at a time** per order, marked before the gate wait.
    - **A change with no answer** counts the larger quantity (Pending MODIFY, a MODIFY_UNCONFIRMED event) until the broker's terms settle it.
    - **Broker terms:** updates carry terms (Kite: quantity, price, trigger), and older streamed terms can't undo a confirmed change. The broker's list counts as current.
    - **A cancel with no answer** stays on its way and is followed up; if the broker's list still shows it working, it didn't take. Pressing cancel again is allowed.
    - **Restart:** changes or cancels on their way come back in doubt.
    - **Gates** re-run the risk checks after a wait.
  - **Checks:** mutation 25/25, after three test gaps were fixed.
- 16 Oct, #260 merged (PR #338); `main` green. **#261 done** (coordinated exits).
  - **Defect confirmed:** loss limits, the square-off and stop algos each sized their close from the position they last saw, and nothing stopped two of them closing the same quantity; together they could turn a long into a short. Resting orders (a target, a stop algo's exit) were not cancelled before a forced close.
  - **The fix:**
    - **Reduce-only:** every closing order is marked; the risk gate refuses one that would close more than is open, counting other reduce-only orders still working (in doubt too).
    - **The exit desk** (one per account, risk/closing.py) handles loss limits and square-off: a lock per position, stop the algos on it, cancel resting closing orders and wait up to 3 s, read the position afresh, leave alone what working closes still own, send only the rest, and say what it left.
    - **Stop algos** cap their exit to what is still open, and finish saying so when nothing is left.
    - **Screens:** the order book marks closes; the square-off message is the server's own summary.
  - **Decided:** a plain sell stays unlimited (it may be meant to open a short); the desk still cancels it before a forced close.
  - **Checks:** 22 new tests, including a loss limit and a square-off at the same moment (closed once, never short); mutation 26/26 after four test gaps were filled.
- 16 Oct, #261 merged (PR #339); `main` green. **#262 done** (STOP, cancel and flatten as separate safe operations).
  - **Defects confirmed first (tests written before the change):**
    - a STOP or the daily-loss stop blocked every new order, exits too, so reducing risk meant resuming trading first;
    - a STOP's cancel-all turned an order the broker never answered into "cancel pending", so the evidence could no longer settle it and it stayed counted forever;
    - the red band claimed "working orders cancelled" even when a cancel failed.
  - **Decided:**
    - **STOP** stays as it was (block, cancel, stop algos). It never closes, and now reports exactly what it did.
    - **Flatten** is a separate, deliberate action allowed while stopped. Only the exit desk's orders get through the stop, and only if reduce-only; they still pass every other risk check.
    - **A position whose size isn't known** (a reconcile alert or a re-check) is never flattened; the band says to close it at the broker.
    - **Who may flatten:** the account's traders, risk and admin; it needs a confirmation and is audited (FLATTEN). One runs at a time per account, so repeated clicks close once.
  - **Screens:** the red band shows the server's report and **Flatten (N)** (two clicks); STOP's report also goes to Messages.
  - **Checks:** 13 new tests plus server tests; mutation 19/19 after adding a "one at a time" test; real browser check passed.
- 16 Oct, **#263 done** (MTM breaches through failures and restarts).
  - **Defects confirmed (tests written first):**
    - which rules fired was in memory only, so a restart could fire a rule again and close twice;
    - a rule counted as fired before its close finished, so a refused or failed close stayed silent the rest of the day;
    - a broken rules file loaded nothing and only said so in the log.
  - **The fix:**
    - **A saved breach per firing:** each firing is saved, with states ALERTED / REQUESTED / WORKING / BLOCKED / UNKNOWN / COMPLETED.
    - **State worked out from orders and positions:** from the close's orders (by basket ids) and the positions, only once a restart's restore is done.
    - **COMPLETED needs the broker's agreement** on the size.
    - **BLOCKED shows Retry** (two clicks); it is never retried by itself, and never while UNKNOWN.
    - **Red bands for closes and data:** BLOCKED / UNKNOWN closes, and unreadable rules or state, raise red bands at any hour.
    - **Bad files:** a broken file is kept aside and named. A bad rule is named while the good ones are still watched. A lost state file holds auto-close for the day.
    - **Saves:** write, flush, rename.
  - **Checks:** 18 new tests, mutation 19/19 (after five test gaps were filled), and a demo restart check (`context/evidence/h-263-mtm-restart.txt`). The demo check found that the alert band only ran in market hours, so MTM problems now run on their own all-day job and once at start.
- 16 Oct (late), #263 merged (PR #341). **Ayush asked to pause until Wednesday 21 Oct.** His weekly usage was at 62% and he wants it to last; he will spend five days understanding the code.
  - **His ask:** fix only what really matters now, without lowering quality (postpone rather than rush), and write documentation explaining everything simply.
  - **Decided:**
    - **Nothing more is merged before Wednesday.** No live trading happens before then, so the remaining P0s can safely wait: #264 lost stops, #265 durable order intents, #267 price freshness, #270 resource ownership.
    - **#264 is parked** half done on branch `h-264-protection`. It has a WIP commit and no PR: the server side only. Still needed: tests, the Restore button, the mutation check, docs.
    - **Wrote `docs/GUIDE.md`:** the whole project in plain words, a five-day reading plan, one order traced through the code, each safety system, what's left, and his own steps. README links to it.
  - **On Wednesday:** start with his questions, then finish #264, then #265, #267, #270.
- 16 Oct (last, on the branch only): #264 tests written and pushed to `h-264-protection` (5 tests; all 1253 pass). They showed the intended behaviour: a restored stop is itself lost at the next restart and flagged again, because only broker-held stops survive outages. Wednesday: the Restore button, mutation check, docs, PR. Ayush's weekly usage is at 65% until Thursday, so be economical: finish #264, then #265, #267, #270.
- 16 Oct (Ayush said to keep going until the 4-hour limit): **#264 done** (lost stops: a durable flag and a guarded Restore).
  - **The defect (test first):** a lost stop was reported once (ALGO_LOST) and then forgotten, so after a second restart an open, unprotected position had no band.
  - **Decided (the guarantee, written in docs and the API):** MockTrade-held stops work only while the server runs; only broker-held stop orders or GTTs survive an outage.
  - **The fix:**
    - **Durable notes:** every flag and every resolution is an audit note (UNPROTECTED / PROTECTION_RESOLVED), rebuilt at each start until resolved.
    - **Restore (two clicks, by someone who trades the account):**
      - it starts the same stop sized to what is open;
      - a bracket comes back as an OCO at its levels;
      - it is refused while rebuilding, while an old order is working or unanswered, while another stop is there, if the position flipped, or if the size is in doubt.
  - **Checks:** 6 tests plus API and UI tests; mutation 12/12. Not yet a real-browser run: do one on Wednesday before relying on it.
  - **Browser check passed** (`context/scripts/e2e-restore-stop.cjs`, screenshot `context/images/h-264-restore-band.png`). The flag survived two demo restarts; Restore needed two clicks and named what it starts; the stop ran again and the band cleared by itself.
  - **The check found a real defect:** the scheduler ran **no** jobs on weekends and holidays, so the lost-stop band and the MTM close warnings never showed then (positions stay open over a weekend). `Scheduler.every(..., any_day=True)` now runs those two all week; trading jobs stay trading-days only.
- 23 Oct, Ayush's limits reset; work resumed. **#265 done** (orders on disk before the broker).
  - **Defects confirmed:**
    - the audit log wrote in the background, after the order was at the broker: a crash in that moment left an order MockTrade forgot, and its id could be reused;
    - serve never wired the log's failure callback;
    - the queue was unbounded.
  - **The fix:**
    - **Written first:** after the checks and before the broker, the order manager waits until the order is on disk (`EventLog.persist`, 2 s limit). A change is written first too, at the larger size (MODIFY_SENDING, audit only).
    - **Disk failing, behind, or unconfirmed:** the order is not sent and says why. Forced closes still go; cancels never wait.
    - **Memory stays bounded:** notes past `max_backlog` are dropped and counted, while order events are always kept.
    - **A band at any hour:** an "audit log" band shows the problem, and it clears itself once the log writes again.
    - **Backtests skip it,** because they never send a real order.
  - **Checks:** 12 tests; mutation 12/12 after one test gap; a real kill -9 drill on the demo passed (the order is known after the crash and its id isn't reused; `context/evidence/h-265-crash-drill.txt`).
- 23 Oct, #265 merged (PR #346). **#267 done** (prices that act on orders).
  - **Defects confirmed:**
    - algos and the paper broker read conflated prices, so a spike through a stop that came back before they read was never seen;
    - a late tick could overwrite newer state;
    - the risk check only wanted a positive price, so a quiet instrument's old price passed while others ticked.
  - **The fix:**
    - **Every tick, in order:** algos and the paper broker read every tick, at most 10,000 unread each. Past that the oldest are dropped and counted, with a WARN band "price overload". Screens stay conflated.
    - **Late ticks:** a tick older (by exchange time) than what we hold is counted and dropped.
    - **Freshness per instrument:** orders that add risk need their own instrument's price at most 60 s old; closing trades always go. Growing a change is checked the same way.
    - **Spreads:** the spreader waits when its leg prices are more than 3 s apart.
  - **Decided (the stop semantics):** a stop is "touched" if any tick reaches it, even when the price comes back before the next read.
  - **Checks:**
    - 9 tests; mutation 12/12;
    - a demo check (`e2e-exits.sh`) still passes;
    - a mistake caught on the way: the mutation script named a test file that didn't exist, which made every mutant look "caught". The scripts now run the tests unmutated first and stop if they fail.
- 23 Oct, **#270 done** (whose is what).
  - **Defects confirmed:**
    - every operational alert went to every signed-in user, including other accounts' Kite login, UNPROTECTED and MTM bands with client ids;
    - backtests had no owner: anyone could list and read anyone's;
    - any user could import candles into the shared dataset.
  - **The fix:**
    - **Alerts carry `account_id`:** the stream sends an account's alerts only to people who may see that account, both on connect and live, and its "cleared" goes to the same people. Permissions are re-read while connected, so revoked access stops at once. Firm-wide alerts still reach everyone.
    - **Backtests have an owner:** admins see all. Someone else's is "no backtest …", the same as a missing one. Old runs without an owner are admin-only.
    - **Candles are a shared dataset:** importing them is admin-only.
    - **The policy is written down:** the "Who sees what" table in `docs/design/admin.md`.
  - **Checks:** two-user tests (stream, backtests, candles) plus tests that each alert producer tags its account; mutation 15/15.
