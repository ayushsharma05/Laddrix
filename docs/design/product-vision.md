# MockTrade: product vision

**Status:** agreed direction (Ayush, 14 Oct 2026). MockTrade is its own product: a professional
trading platform designed for the **Indian market** (NSE, BSE; equities, futures, options),
built to the standard of the best professional trading software, and deployable commercially
later. Today the only paid service is the broker API (Zerodha Kite Connect, ₹500/month);
everything else is built in-house.

It names, copies and references no other vendor's product. Designs use common industry
patterns (price ladders, order tickets, spreaders, audit trails) in our own way.

---

## 1. Principles

1. **Every component is independent and replaceable.** Modules talk only through interfaces
   (ports) with adapters behind them. Swapping a broker, a data feed, the database, a widget or
   an algo never touches another module's insides.
2. **Backend provides, frontend displays.** The server describes what is possible (order types
   and their fields, validities, products, tick/lot, limits, permissions, algo parameters) and
   the screens render from that description. Nothing market- or broker-specific is hard-coded
   in the UI. A new order type, product, exchange or algo is a backend change.
3. **Safety before speed, speed before features.** Every order passes the same risk gate and
   throttle; nothing is sent blind; cancels always work; every event is audited.
4. **Honest about limits.** 5-level depth, ~1 tick/s, broker rate limits, SEBI rules: shown in
   the product, never hidden.
5. **Professional ergonomics.** Dense, fast, keyboard-friendly, one click for the common action,
   never one accidental click for the dangerous one. Every state written in words.
6. **Measured, not guessed.** Latency, load and fill quality are measured and published.
7. **Compliance by design.** Ready for SEBI's retail-algo framework (algo IDs, order tagging,
   OPS caps), exchange empanelment and licensed data before serving other users.

## 2. Replaceable parts (ports and adapters)

| Part | Interface (port) | Today | Can be replaced by |
|---|---|---|---|
| Broker | `Broker` protocol (place, modify, cancel, updates, orders, positions, `order_spec`) | Kite adapter, paper broker, fake | any Indian broker API, direct exchange access |
| Market data | `MarketData` + feed handlers producing `Tick` | Kite WebSocket feed, simulator | licensed data vendor, exchange feed, replay file |
| Order spec | `order_spec(instrument)` per broker | standard NSE/BSE spec | broker- or exchange-specific specs |
| Reference data | `InstrumentStore` | Kite instrument dump | exchange master files, vendor |
| Risk | `OrderGate` chain (risk checks, throttle, kill switches) | in-process | an external pre-trade risk service |
| Storage | `EventLog`, `UserStore` | SQLite (WAL) | PostgreSQL, cloud databases |
| Positions/P&L | `PositionBook` from fills | in-process | a separate accounts service |
| Algos | algo engine API (start, pause, resume, stop, params from a spec) | in-process strategies | out-of-process algo servers, user-written strategies |
| Transport to screens | REST + one WebSocket (`stream.md` contract) | FastAPI | any server that speaks the contract |
| Screens | widgets registered in a workspace, props in, no cross-widget access | React + dockview | desktop shell, phone app, third-party widgets |
| Auth | password + TOTP sessions | in-house | SSO / OIDC |
| Notifications | notifier port | in-app messages | email, Telegram, push |

## 3. Feature catalogue

What a professional Indian-market platform provides, grouped. ✅ built · 🔜 planned milestone ·
💡 later.

### Trading screens
- ✅ Price ladder: 5-level depth, click to trade, working orders, queue estimate, volume at price, confirm-over
- ✅ Order ticket: every order type the broker offers, value, worst-case position, confirm-over
- ✅ Order book, positions with reconcile state, audit trail with timelines, messages, watch list
- 🔜 **Option chain** with live prices, OI, change in OI, IV and Greeks (delta, gamma, theta, vega); click to ticket/ladder
- 🔜 **Strategy builder**: multi-leg structures (spreads, straddles, strangles, butterflies, condors, ratio spreads) with payoff chart, breakevens, max profit/loss, margin, then one-click send as legs or via the spreader
- 🔜 **Basket orders**: build, check margin, send together
- 🔜 Time & sales (from the tick stream), market depth window, market movers / heat map
- 🔜 **Charts** (broker historical data + live ticks), trade marks on the chart, click-to-trade from the chart
- 🔜 Alerts: price, % move, volume, OI, spread level; shown in-app and pushed
- 💡 Scanners / screeners on live data

### Orders and execution
- ✅ LMT, MKT (with protection), SL, SL-M; DAY/IOC; CNC/MIS/NRML (from the order spec)
- 🔜 Server-side **synthetic orders**: trailing stop, OCO, bracket (entry + target + stop), good-till-triggered alerts-to-order
- 🔜 Broker **GTT** (good till triggered) where the broker offers it, through the order spec
- 🔜 Execution algos: **slicer**, iceberg, TWAP, VWAP, participation (POV)
- 🔜 **Spreader**: two-leg and multi-leg calendar/inter-month spreads with legging control and pay-up
- 💡 Smart order routing between NSE and BSE for equities

### Risk and control
- ✅ Pre-trade checks with readable reasons, throttle under broker and SEBI caps, kill switches (user, account, firm), auto-stop on daily loss
- 🔜 **Admin panel**: users, roles, accounts, per-user limits without code
- 🔜 Margin view and pre-trade margin check (broker margin API)
- 🔜 MTM monitoring: per-position and portfolio stop, square-off assistant before the MIS cut-off
- 💡 Scenario risk (price/IV shocks) on the options book

### Accounts, funds and reporting
- ✅ Positions and P&L from fills, reconciled with the broker
- 🔜 Holdings, funds and margins (broker APIs), tradebook
- 🔜 **Charges estimator**: brokerage, STT/CTT, exchange transaction charges, SEBI fee, stamp duty, GST, so P&L is shown gross and net
- 🔜 End-of-day report, P&L by day/instrument/strategy, CSV/PDF export, tax P&L view
- 💡 Transaction-cost analysis (slippage vs arrival price) for algos

### Research and practice
- ✅ Paper trading against live prices with conservative fills
- 🔜 **Tick recorder and replay**: record the day, replay it into paper trading and algos
- 🔜 **Backtesting** of algos on recorded or historical data, with the same engine as live
- 💡 Strategy notebook / journal

### Workspace and apps
- ✅ Dockable widget workspace, link groups, saved layouts, PAPER/LIVE strip, status lights, STOP
- 🔜 Desktop app with pop-out windows on any monitor
- 🔜 Phone: watch, positions, cancel, close, STOP (no new trades from a small screen)
- 🔜 Keyboard shortcut editor, themes (dark/light, colour-blind safe)

### Platform and operations
- ✅ Multi-user, multi-account design; password + TOTP; audit of everything
- 🔜 Docker + HTTPS deployment in Mumbai with a static IP; backups; runbooks; morning routine
- 🔜 Latency and load measured and published; health and metrics
- 💡 Horizontal scale (order managers sharded by account), PostgreSQL, message bus

### Compliance (before serving other users)
- Broker multi-user arrangement; exchange empanelment as an algo provider; algo IDs on every algo order; licensed market data for shared prices; Research Analyst registration for black-box strategies; data retention and audit export. See `kite-architecture.md` §0.

## 4. Roadmap

| Milestone | Outcome |
|---|---|
| K5 (finishing) | ticket, desktop app |
| K6 Algos | engine, slicer, spreader, algo manager |
| K7 Deploy & operate | Docker/HTTPS, Mumbai server, latency, runbooks |
| K8 Options | option chain with Greeks, strategy builder with payoff, basket orders |
| K9 Orders+ | synthetic orders (trailing, OCO, bracket), GTT, iceberg/TWAP/VWAP |
| K10 Accounts & reports | holdings, funds, margins, charges, EOD and P&L reports |
| K11 Research | tick recorder, replay, backtesting |
| K12 Charts & alerts | charts with trade marks, alerts, time & sales, movers |
| K13 Admin & phone | admin panel, per-user limits, phone screens |

Each milestone follows the same process: issue → brainstorm the traps → code → tests →
mutation check → real-browser check → PR, with the context folder updated at each step.
