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
