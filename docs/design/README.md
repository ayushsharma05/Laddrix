# Design

How MockTrade is built. Each doc is checked against the code; the diagrams' sources are in `context/images/*.mmd`.

**Start here**

| Doc | What it covers |
|---|---|
| [product-vision.md](product-vision.md) | What MockTrade is for, and its rules ("backend provides, frontend displays", every part replaceable) |
| [kite-architecture.md](kite-architecture.md) | The whole system on Kite Connect: the parts, the price path, the order path, deployment |
| [glossary.md](glossary.md) | Every term, in plain words |
| [data.md](data.md) | What data we hold and its class, logs without secrets, how long each kind is kept, one person's export and erase, the audit trail's tamper evidence |
| [domain-model.md](domain-model.md) | The main types and how they relate |
| [order-life.md](order-life.md) | An order's states and the key journeys: partial fill, modify, cancel, reject, restart, disconnect, STOP |

**By area**

| Doc | Area |
|---|---|
| [ui.md](ui.md), [stream.md](stream.md) | The screens, widgets, keyboard shortcuts, themes; the browser stream |
| [algos.md](algos.md), [gtt.md](gtt.md), [options.md](options.md) | Algos, GTT orders, options pricing, chain and strategies |
| [portfolio.md](portfolio.md), [charges.md](charges.md), [margin.md](margin.md), [mtm.md](mtm.md), [reports.md](reports.md) | Funds and holdings, charges, margin, MTM limits and square-off, end-of-day reports |
| [charts.md](charts.md), [research.md](research.md) | Charts and alerts; recorded ticks, replay, backtests |
| [admin.md](admin.md) | Users, roles, accounts and limits |
| [security.md](security.md) | Threat model, permission matrix, abuse cases, controls mapped to evidence or gaps (#279) |
| [compliance.md](compliance.md) | Surveillance alerts for risk, the auditor's export, SEBI's algo rules and the exchange algo id |
| [fills.md](fills.md) | Exact fill economics: averages to the fraction, exact fill values, broker corrections |
| [expiry.md](expiry.md) | F&O expiry day: warnings, cut-offs, refusals, settlement, restarts |
| [performance.md](performance.md) | Measured speed |

Operations are in [`../runbooks/`](../runbooks/README.md), the test plan is in [`../test/`](../test/README.md), and decisions are in [`../adr/`](../adr/README.md).
