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
