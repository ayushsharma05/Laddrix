> **Paused (14 Oct 2026):** this brief is for the COMEX/MockCME track. The active track is NSE/BSE on Kite Connect: see `docs/design/kite-architecture.md`.

# Laddrix — Product brief

Laddrix is a professional-style trading platform for COMEX metals futures that trades against a mock exchange replaying real market data. It is built for one spread trader first, with the architecture of a full multi-user, multi-site platform.

Status: draft for review (issue #10). Sources: trader interview (#8, #9).

## Scope

| In scope | Out of scope (for now) |
| --- | --- |
| COMEX gold (GC), silver (SI), copper (HG) futures | Real exchange connectivity, real money |
| Outrights and **multi-leg structures**: calendar spreads, butterflies, condors | Charts widget (separate project later) |
| Mock exchange(s) that match orders and replay real historical data | Options, other asset classes |
| Day and **GTC** (good-till-cancelled) orders | Mobile app |

## Users

| User | Needs |
| --- | --- |
| Spread trader (primary) | Trade outrights and structures fast, mostly through the spreader and algos; see risk and P&L live |
| Admin | Set users, permissions and risk limits without touching code |
| Risk / support (later) | See every order's history; trust that limits always hold |

## Core user stories

Short form: *As a trader, I want … so that …*. Full Given/When/Then criteria are written in each story's build issue.

| ID | Story | Release |
| --- | --- | --- |
| US-01 | Place, modify and cancel limit orders on an outright, so that I can trade by hand | v0.1–v0.2 |
| US-02 | Choose Day or GTC; GTC orders survive restarts and the next day, so that I don't re-enter working orders | v0.4 |
| US-03 | Trade from a live depth ladder by clicking a price, so that I act in one click | v0.3 |
| US-04 | See Time & Sales (date, time, price, qty, product, exchange, contract), so that I read the tape | v0.3 (#120) |
| US-05 | Define structures with N legs and ratios (spread, fly, condor), so that I trade what I actually trade | v0.5 |
| US-06 | See a synthetic ladder for any structure, built from its legs, so that I can trade it even when the exchange isn't quoting it | v0.5 |
| US-07 | Have the spreader work the quoting leg and hedge the others on fill, with a pay-up I set, so that I'm rarely left unhedged | v0.5 |
| US-08 | Orders breaking my max order size or max position are rejected before the exchange, so that a mistake can't hurt me | v0.2 |
| US-09 | Admins set limits per user without code, so that limits change safely | v0.7 |
| US-10 | See exchange messages and internal limit alerts, so that I know why something happened | v0.2 |
| US-11 | See positions and P&L per contract and in total, clearly and live, so that I manage risk | v0.4 |
| US-12 | Get configurable fill alerts and choose whether orders need confirmation, so that I notice fills and set my own safety | v0.4 (#121) |
| US-13 | At end of day see settlement P&L, positions and open orders, so that I close the day cleanly | v0.7 |
| US-14 | My multi-window workspace reopens exactly as I left it, so that I start trading immediately | v0.8 |
| US-15 | See the full history of any order (audit trail), so that I can explain every fill | v0.8 |

## Hard numbers (laptop scale)

| Area | Target | Why |
| --- | --- | --- |
| Click → mock exchange acknowledgement | p99 < 20 ms | Feels instant to a human |
| Leg fill → hedge order sent (inside the robot) | < 1 ms | Hedging is a race |
| Price change → ladder updated | < 100 ms | Faster than the eye notices |
| Price change → positions / P&L updated | < 1 s | Risk view stays current |
| Order throughput | 200 orders/s | Comfortably above one trader plus algos |
| Market data replay | 20,000 updates/s | Busy real-market bursts |
| Legging | Hedge filled within the trader's pay-up in ≥ 90% of structure fills | From the interview; confirm |
| Reliability | No accepted order is ever lost; GTC orders survive restarts | Trust |
| Safety | No order reaches an exchange without passing risk checks | Non-negotiable |
| Audit | Every order event recorded with time and reason | Explain every fill |

p99 = 99 out of 100 cases are at least this fast.

## Decisions the interview forced

1. **Multi-leg structures from the start of the design** → domain model (#21), products ADR (#19), synthetic pricing (#61), spreader (#62).
2. **GTC orders** → persistence (#56), end-of-day jobs (#93), message fields (#28).
3. **Limits reject, admin-configurable** → risk checks (#45), admin panel (#96).
4. **Alerts are messages** (exchange + internal) → message dictionary (#28).
5. **Charts are out of scope** until after v1.0.

## Open questions

- **Day P&L definition:** measured from yesterday's settlement price or from today's entry prices? → glossary (#20).
- **Legging target:** is "≥ 90% hedged within pay-up" the right measure? → confirm in review.
