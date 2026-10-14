# MockTrade status and next steps

As of 14 Oct 2026. GitHub issues are the source of truth.

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
| K2 Paper broker (#133–#135) | next | ⏳ |

Merge order: #123 → #157 → #158 → #159 → … (GitHub retargets stacked PRs automatically when their base merges).

**Design changes made while coding:**
- Kite REST uses **our own async httpx client** (not the synchronous SDK); the SDK stays only as a test reference (decoder equivalence test).
- `.gitignore` ignores only the top-level `/data/` (it had hidden `tests/data/`).
- CI uses actions/checkout@v5 and setup-python@v6 (Node 20 deprecation).
- Decoder measured at ~17 µs per full packet (≈1 % of a CPU at 750 ticks/s).

**UI plan (promised 14 Oct):** before any UI code, a UI design pass with mockups for approval: dark-first design system, workspace model (dock/tab/pop-out/linked widgets), every screen (ladder, ticket, blotter, positions, audit, algo manager, admin, morning login), interaction and safety rules.

## Next
1. Merge PR #123 (context folder), then the Kite architecture PR.
2. **#124 K0.1**: package skeleton. Claude has given the code and line-by-line explanation (also verified: 15 tests + ruff pass for #124 + #125 together).
3. Then #125 (money + instruments), #126, #127.
4. Before K1: create the Kite Connect app (₹500/month data plan) at developers.kite.trade.

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
