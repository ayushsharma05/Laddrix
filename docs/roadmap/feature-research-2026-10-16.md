# Later feature coverage and implementation handoff

Research date: 16 October 2026. Baseline inspected: main `dfce3e426d0741fabb99dd84c6cafe6ef604f821`. This is a planning snapshot; re-read current code and live issue state before implementation.

## Direction

Ayush requested a broad public-documentation and customer-feedback review of seven trading product ecosystems and detailed later issues for useful additions. #297 is the expansion tracker, linked from #241. No application code was changed. Do not begin this expansion while core #240 and hardening #257 are unfinished; production security #279 remains a release prerequisite. Existing work #242–#255 stays intact.

All requirements remain vendor-neutral under repository policy. No employer documents, screenshots, code or internal details were used in this handoff. Product names and public citations belong to the accompanying research conversation, not repository files.

## Evidence and limitations

The research examined public manuals, feature catalogues, support articles and available customer reviews. It was not a paid-account walkthrough, benchmark, security audit or proof that every private feature is captured. Features may vary by product surface, subscription, broker, market and date. Where marketing and FAQ pages disagree, treat availability as unverified until checked against the relevant surface. Do not derive support for an API feature from availability in a provider's own UI.

Customer feedback was qualitative and sometimes historical. Useful themes were clear chart/order state, fewer steps in frequent journeys, understandable costs, paper-versus-live expectations, login recovery and support visibility. Positive comments also favoured simple navigation, analysis depth and reusable automation. These observations motivate tests; they do not establish present defect frequency. Independent customer evidence for specialist institutional products was particularly sparse; employee reviews and vendor testimonials were not treated as independent customer validation.

## Existing coverage and foundations

| Feature family | Baseline / boundary | Work tracking |
| --- | --- | --- |
| Order ticket, ladder, paper/live brokers, positions, funds, holdings and trade reporting | Existing foundations; production readiness is not implied | #240, #257, #279 |
| Watchlists, expiry search, notes, instrument discovery and workspace presets | Existing workspace plus planned extensions | #242, #243, #284, #285 |
| Price/compound alerts and persistent delivery history | Existing alerts plus planned shared conditions and inbox | #244, #246 |
| Initial indicators, chart drawings and synchronized templates | Planned foundation; later catalogue and interaction expand it | #245, #301, #310 |
| Trailing/OCO/bracket, slicing, time/volume participation and basic two-leg execution | Built-in foundations; advanced lifecycle and general structures remain separate | #247, #248, #250, #251, #306 |
| Visual no-code strategies; user-code sandbox | Planned visual engine; code upload remains an explicit decision | #249, #292, #307 |
| Paper simulation, recordings/replay, historical tests, sweeps and walk-forward research | Existing basic research; realistic data/fills and validation remain essential | #253, #298, #322 |
| Journaling, transaction-cost analysis and reconciled economics | Planned foundation; investment accounting extends it | #252, #275, #311 |
| Spreadsheet/read API, additional brokers and account dashboards | Planned foundation; external execution signals are separate | #254, #255, #308, #319 |
| Authentication/admin, tenant isolation, durable trading and recovery | Existing foundations with open security/hardening work | #257, #279 |
| Phone management, expiry, operations, release and compliance | Preserve current bounded scope and gates | #283, #287, #289, #290, #291 |

## Added coverage

A = data foundation; B = analysis/portfolio/usability; C = advanced automation/execution; D = explicit product/access decision before implementation; E = validation throughout. These are dependency groups, not promised versions or dates. Split each broad package into bounded implementation issues when ready.

| Issue | Added feature family | Lane |
| --- | --- | --- |
| #298 | Data foundation: licensed history, reference data and reproducible datasets | A |
| #299 | Market-wide scanners, formula watchlists and saved screens | B |
| #300 | Market research workspace: financials, events, news and institutional activity | B |
| #301 | Advanced charts: catalogue, comparisons, historical navigation and chart interaction | B |
| #302 | Order-flow and richer depth with truthful feed semantics | C |
| #303 | Options analytics: volatility surfaces, OI history and combined-premium views | B |
| #304 | Scenario-based strategy discovery and draft portfolios | B |
| #305 | Dynamic options leg selection and contract resolution | C |
| #306 | Options strategy lifecycle: re-entry, rolling and combined profit controls | C |
| #307 | Strategy templates, versioning, explanations and deployment diagnostics | C |
| #308 | Authenticated inbound signals and scoped execution API | C |
| #309 | Broker capability catalogue, recurring investments and advanced order workflows | C |
| #310 | Fast trading workflows: chart orders, scalper panel and safe bulk actions | C |
| #311 | Portfolio analytics, statement imports and investment accounting | B |
| #312 | Onboarding, user-facing incident diagnostics and support workflows | B |
| #313 | Trading discipline: checklists, session budgets and cooling-off controls | B |
| #314 | Decision: consent-based performance sharing and a strategy template marketplace | D |
| #315 | Decision: full mobile order entry beyond the monitoring companion | D |
| #316 | Decision: scoped research assistant and natural-language strategy drafts | D |
| #317 | Portfolio risk scenarios and controlled dynamic hedging | C |
| #318 | Specialist execution: volatility quoting and ratio arbitrage strategies | D |
| #319 | Decision: institutional connectivity, drop-copy reconciliation and delegated desks | D |
| #320 | Decision: ETF basket valuation and inventory-limited market making | D |
| #321 | Decision: broader asset coverage and broker-service integrations | D |
| #322 | Research follow-through: feature coverage, usability and release evidence | E |

## Cross-package dependencies

- #298 supplies licensed and reproducible inputs to #299–#304 and research #253; #278 decides cost/rights/deployment. Restricted feeds must not be silently shared across users.
- #299 uses #242/#246; #301 extends #245; #302 needs feed capability guarantees from #298/#267/#277.
- #303 supplies modelled analysis to #304/#305/#317. #305 and #306 extend #249/#250/#251, not separate order engines.
- #307 makes strategy versions inspectable; #308 uses those versions and scoped credentials, beyond the read-only-first API #254.
- #309 and #310 reuse order/risk services and coordinated exit semantics. #311 builds on exact reconciliation #275.
- #312/#313 turn user feedback into understandable operation and durable optional discipline controls.
- #314–#316 require explicit product decisions. #315 must not silently expand the no-new-entry policy of #283. #316 must not authorize autonomous execution or bypass #292.
- #317 needs credible models and execution safety before automatic hedging. #318–#321 require access, licensing, business and applicable regulatory feasibility before live implementation.
- #322 tracks coverage and real journey evidence throughout, with honest gaps and no blanket parity/reliability claim.

## Boundaries and deferred decisions

A recurring investment schedule is an application workflow; custody, lending, funding, IPO applications and fund distribution require authorized provider capabilities. Track them through #321 rather than pretending Laddrix becomes a broker. Marketplace payments, public performance sharing, full mobile entry, model-assisted drafts, institutional connectivity and market making are decisions, not silently approved launch scope.

No public undocumented feature is asserted absent merely because it was not found. No synthetic candle, sampled order book, paper valuation or estimated margin is represented as an authoritative trade or balance. Special execution features do not promise risk-free arbitrage, institutional latency or profit.

## Implementation handoff

Read current context and issues first. Keep backend-described capabilities and replaceable interfaces. Use existing risk, ownership, intent, reconciliation and audit paths. Specify costs, limits, failure states and acceptance evidence before implementation; follow the repository design/mockup and CI workflow. Close feature issues only on completed evidence or an explicit owner scope decision, never merely because this matrix was added.
