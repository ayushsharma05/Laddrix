"""Additions so the backlog covers every box of the reference one-chart architecture."""
import sys
sys.path.insert(0, '.')
from backlog import body

NEW_MILESTONES = [
 ("v0.6 · Multi-site & routing", "A second exchange at a second site; sessions, throttles, disconnects, reconcile, firm-wide risk."),
 ("v0.7 · Platform services", "Reference data, event log, accounts pipeline, users & permissions, admin, broadcaster."),
 ("v0.8 · Gateways, tools & screens", "Feed adapters, gap recovery, practice mode, FIX and broker routes, SDK, scripted strategies, more screens."),
]
# milestone keys: 'v06','v07','v08','v10'
A = []
def add(title, ms, labels, goal, why, covers, done, deps, steps=None):
    extra = "## Architecture boxes this builds\n" + "\n".join(f"- {c}" for c in covers)
    A.append((title, ms, labels, body(goal, why, None, steps or ["Branch → code + tests → PR → review → merge."],
              done, deps=deps, later=True, extra=extra)))

STD = ["Tests pass in CI", "You can explain every line"]

# ---------------- v0.6 Multi-site & routing  (#73–#85)
add("Second mock exchange at a second site", "v06", ["type:build", "comp:mockcme", "comp:deploy"],
 "Run a second MockCME instance as a different exchange at a second 'site', with its own products.",
 "The reference design has one set of fast components per exchange site. Two sites make routing, multi-site risk and drop copies real.",
 ["Other exchanges, each at its own site", "One guard/runner/newsroom set per site"], STD, ["#64"])
add("Routing table: instrument → exchange → that site's guard", "v06", ["type:build", "comp:appserver"],
 "The reception desk looks up which exchange an instrument trades on and sends the order to that site's guard.",
 "One central front desk serves every exchange site.", ["Order gateway client: routing table"], STD, ["#73"])
add("Runner: exchange session pool", "v06", ["type:build", "comp:sor", "comp:mockcme"],
 "Several exchange sessions per runner with logon, heartbeat, sequence numbers and resend of missed messages.",
 "Real exchanges talk in sessions; sequence numbers are how nothing gets lost.", ["Runner: session pool"], STD, ["#73"])
add("Runner: exchange message throttle", "v06", ["type:build", "comp:sor"],
 "Enforce the exchange's messages-per-second limit per session: queue or reject when full.",
 "Breaking an exchange's limit gets a firm disconnected.", ["Runner: exchange throttle"], STD, ["#75"])
add("Runner: disconnect handling and cancel-on-disconnect", "v06", ["type:build", "comp:sor", "comp:mockcme"],
 "MockCME cancels working orders when a session drops; the runner reconnects, recovers the gap and asks the guard to reconcile.",
 "Disconnects happen; the platform must end up knowing the truth.", ["Runner: disconnect handling", "Exchange cancel-on-disconnect"], STD, ["#75"])
add("Guard: round-robin across several runners", "v06", ["type:build", "comp:rom"],
 "The guard spreads orders across several runners in turn, with a throttle per runner.",
 "Spreading load keeps each exchange session under its limit.", ["Guard: request handler with round robin"], STD, ["#75", "#76"])
add("Guard: reconcile with the exchange", "v06", ["type:build", "comp:rom"],
 "On start, on reconnect and periodically, ask the exchange for open orders, compare with the guard's books, fix and alert.",
 "The guard's notes must always match the exchange.", ["Guard: reconcile handler"], STD, ["#77"])
add("Guard: position reserve", "v06", ["type:build", "comp:rom"],
 "Working orders reserve room in position limits; released on fill or cancel.",
 "Stops two working orders from together breaking a limit.", ["Guard: position reserve handler and book"], STD, ["#45"])
add("Guard: expired contract handling", "v06", ["type:build", "comp:rom"],
 "At contract expiry, clean up orders and positions and block new orders in expired contracts.",
 "Futures expire; the books must not keep dead contracts.", ["Guard: expired contract manager"], STD, ["#73"])
add("Guard: kill switch, daily loss limit and order-rate limit", "v06", ["type:build", "comp:rom"],
 "Add the remaining standard risk checks: a firm/trader kill switch, a daily loss limit and an orders-per-second limit.",
 "These are the checks that stop disasters, not just mistakes.", ["Guard: risk limit handler (full list)"], STD, ["#45", "#54"])
add("Guard-to-guard link: firm-wide risk across sites", "v06", ["type:build", "comp:rom"],
 "Guards at different sites share total exposure so firm-wide limits hold across exchanges.",
 "Our own design for the guard-to-guard link seen in the reference (its real purpose is unknown).", ["Guard-to-guard link"], STD, ["#73", "#82"])
add("Participant ID registration", "v06", ["type:build", "comp:mockcme", "comp:sor"],
 "MockCME requires each trader ID to be registered before it accepts orders; a small service registers them and the runner attaches the reference.",
 "Mirrors exchange rules that every order must identify who is behind it.", ["Participant ID registration service"], STD, ["#75"])
A.append(("Release v0.6", "v06", ["type:release"], body("Tag `v0.6` with release notes.", "Marks multi-site routing as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.6 published"], deps=["#73", "#74", "#75", "#76", "#77", "#78", "#79", "#80", "#81", "#82", "#83", "#84"])))

# ---------------- v0.7 Platform services  (#86–#99)
add("Reference data service: instruments, products, spreads, strategies", "v07", ["type:build", "comp:common"],
 "A service that owns the master list of instruments, products, spread definitions and uploaded strategies, and pushes changes to subscribers.",
 "Every component needs the same contract details; one owner avoids disagreement.", ["Reference data / master list service"], STD, ["#85"])
add("Newsroom: data store and recent-data cache", "v07", ["type:build", "comp:mds"],
 "The newsroom loads contract details from the reference data service and keeps recent snapshots in an LRU cache.",
 "Lets new subscribers get a picture instantly.", ["Newsroom: data store + LRU cache"], STD, ["#86"])
add("Event log (careful lane)", "v07", ["type:build", "comp:common"],
 "A durable, ordered log that services write order and fill events to; readers can replay from any point.",
 "The careful lane: nothing is lost, and the accounts office can catch up after a crash.", ["Event log topics (careful lane)"], STD, ["#26", "#85"])
add("Accounts as a stream pipeline", "v07", ["type:build", "comp:accounts"],
 "Rebuild the accounts office as small stream stages reading the event log: re-key by account → fills filter → net position → manual adjustments → P&L.",
 "Matches the reference pipeline; each stage is simple and testable.", ["Repartition", "Fills filter", "Net position calculator", "Position modifications", "P&L calculator"], STD, ["#88", "#54"])
add("Drop copy readers per source", "v07", ["type:build", "comp:accounts"],
 "A reader per drop-copy source (each exchange, the mock broker) that turns raw messages into clean fill events on the event log.",
 "Independent witnesses for every fill.", ["Drop copy parsers"], STD, ["#88", "#55"])
add("Firm order book copy and query API", "v07", ["type:build", "comp:accounts"],
 "A fast store holding a live copy of every firm order, with an API to search it.",
 "Screens and tools can query orders without asking the guard.", ["Order book stream → fast store → query API"], STD, ["#88"])
add("Compliance alerts", "v07", ["type:build", "comp:accounts"],
 "A stream stage that flags rule breaks (e.g. self-trades, oversized orders, unusual cancel rates) and records them.",
 "Every trading firm must watch its own trading.", ["Compliance alert stream"], STD, ["#88"])
add("End-of-day jobs", "v07", ["type:build", "comp:accounts"],
 "Scheduled jobs: settlement prices, daily P&L snapshot, contract rolls, reports.",
 "The trading day must close cleanly.", ["Scheduled jobs"], STD, ["#89"])
add("User setup: users, accounts, permissions and limits", "v07", ["type:build", "comp:appserver"],
 "Create users and trading accounts, set what each may trade and their limits.",
 "Real platforms have many users with different rights.", ["User setup service", "Permission data"], STD, ["#86"])
add("Reception desk: multi-user login, sessions and permission check", "v07", ["type:build", "comp:appserver"],
 "Several users log in; sessions track user, desk and admin impersonation; every request is checked against permissions.",
 "Turns the one-user skeleton into a real front desk.", ["Auth & session manager", "Permission check", "Widget request router"], STD, ["#94"])
add("Admin panel", "v07", ["type:build", "comp:ui"],
 "A screen for admins: users, limits, kill switch, service status.",
 "Operations need control without touching code.", ["Admin server and dashboard"], STD, ["#94", "#82"])
add("Live broadcaster for orders, fills and positions", "v07", ["type:build", "comp:appserver", "comp:ui"],
 "A broadcaster service with channels (orders, fills, positions) and a small client library the screens subscribe with.",
 "Pushes many updates to many screens efficiently.", ["Broadcaster server and data adapter", "Broadcaster client library"], STD, ["#88", "#89"])
add("Shared settings service", "v07", ["type:build", "comp:appserver"],
 "Stores per-user screen layouts and settings; the reception desk loads them at login.",
 "Our own design for the reference's settings/cache servers (their exact role is unknown).", ["Config / cache servers"], STD, ["#95"])
A.append(("Release v0.7", "v07", ["type:release"], body("Tag `v0.7` with release notes.", "Marks platform services as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.7 published"], deps=["#86", "#87", "#88", "#89", "#90", "#91", "#92", "#93", "#94", "#95", "#96", "#97", "#98"])))

# ---------------- v0.8 Gateways, tools & screens  (#100–#112)
add("Newsroom feed adapters: two feed formats", "v08", ["type:build", "comp:mds", "comp:mockcme"],
 "MockCME publishes a binary direct feed and a FIX-style feed; the newsroom has one adapter per format feeding the same book builder.",
 "The adapter pattern: a new exchange format only needs a new adapter.", ["Feed handlers (direct and FIX)"], STD, ["#99"])
add("Newsroom: gap detection and snapshot recovery", "v08", ["type:build", "comp:mds", "comp:mockcme"],
 "Detect missing sequence numbers, mark the book stale, recover from a snapshot, resume.",
 "Lost packets must never leave a silently wrong book.", ["Gap detection & recovery"], STD, ["#100"])
add("Practice mode: practice runner and simulator", "v08", ["type:build", "comp:sor", "comp:mockcme"],
 "A practice environment where the runner sends orders to a simulator that fills against live prices.",
 "Same platform, pretend fills: how traders practise safely.", ["Practice runner", "Simulator exchange"], STD, ["#99"])
add("Queue-position estimate on the ladder", "v08", ["type:build", "comp:mds", "comp:ui"],
 "Estimate how many lots are ahead of each working order and show it on the ladder.",
 "Traders decide whether to wait or move based on it.", ["Queue-position estimator"], STD, ["#51", "#52"])
add("FIX gateway and FIX risk adapter", "v08", ["type:build", "comp:rom"],
 "Outside systems can send orders in FIX; a risk adapter checks them before they reach the guard.",
 "A standard door for external clients.", ["FIX gateway", "FIX risk adapter"], STD, ["#99"])
add("Broker route: mock broker gateway and broker drop copy", "v08", ["type:build", "comp:sor", "comp:mockcme"],
 "A second way out: orders routed through a mock broker instead of directly to the exchange, with the broker's own drop copy.",
 "Mirrors routing through a third-party platform.", ["Broker order router", "Broker drop copy"], STD, ["#99"])
add("Client SDK and an external algo app", "v08", ["type:build", "comp:algos", "comp:common"],
 "A small SDK for outside programs (market data, orders, position reserve) and one external algo app built with it.",
 "Shows how separate apps plug into the platform.", ["SDK client algo server", "Guard: position-reserve request"], STD, ["#80", "#99"])
add("Scripted strategies in a sandbox", "v08", ["type:build", "comp:algos"],
 "Run user-uploaded strategy scripts in a sandbox with the same lifecycle events and a loop guard against runaway code.",
 "Lets traders write their own robots safely.", ["Scripted-algo server", "Sandbox", "Loop guard"], STD, ["#86", "#59"])
add("Strategy upload tool", "v08", ["type:build", "comp:algos"],
 "A command-line tool that validates a strategy script, uploads it to the reference data service, and shows its logs.",
 "The developer workflow for scripted strategies.", ["Strategy editor tool", "Upload server with validation"], STD, ["#107"])
add("Spreader: quoting-leg selection and re-pricing", "v08", ["type:build", "comp:algos"],
 "Choose which leg to work (usually the less liquid) and re-price the working leg when the other leg moves.",
 "Completes the spreader's real behaviour.", ["Spreader: quoting-leg selector, working-leg manager"], STD, ["#62"])
add("Screens: audit trail and hedge manager", "v08", ["type:build", "comp:ui"],
 "An audit trail widget (every event of an order) and a hedge manager widget.",
 "The audit trail is how traders and support see exactly what happened.", ["Audit trail widget", "Hedge manager widget"], STD, ["#91"])
add("Screens: multi-tab workspace with saved layouts", "v08", ["type:build", "comp:ui"],
 "One workspace window with tabs and several widgets, layouts saved per user.",
 "The way professional trading screens are organised.", ["Widget workspace", "Saved layouts"], STD, ["#98"])
A.append(("Release v0.8", "v08", ["type:release"], body("Tag `v0.8` with release notes.", "Marks gateways, tools and screens as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.8 published"], deps=["#100", "#101", "#102", "#103", "#104", "#105", "#106", "#107", "#108", "#109", "#110", "#111"])))

# ---------------- v1.0 additions  (#113–#118)
add("Several reception desks behind a load-balancing gate", "v10", ["type:build", "comp:deploy", "comp:appserver"],
 "Run multiple reception desks behind a gate that spreads users, checks health and drains a desk for maintenance.",
 "No single desk can take the platform down.", ["Office gate / load balancer", "Maintenance drain"], STD, ["#68", "#95"])
add("Environments: live-sim, dev and cert", "v10", ["type:build", "comp:deploy"],
 "Separate environments with their own configuration and data, selectable at start-up.",
 "Testing must never touch the 'live' environment.", ["Environments"], STD, ["#68", "#102"])
add("Deployment process: tags, approval and maintenance windows", "v10", ["type:build", "comp:deploy", "comp:ci"],
 "Deploy a tagged version only after approval and only inside each exchange's maintenance window.",
 "How real platforms change safely.", ["Delivery pipeline"], STD, ["#114"])
add("Status monitor page", "v10", ["type:build", "comp:ui", "comp:deploy"],
 "One page showing every service's state: up, ready, starting, maintenance, down.",
 "Operators see the whole platform at a glance.", ["Service monitor"], STD, ["#66"])
add("Central log search", "v10", ["type:build", "comp:deploy"],
 "All services' logs collected in one searchable place.",
 "Finding why an order failed should take seconds.", ["Log store and search"], STD, ["#65"])
add("Shared service skeleton for every service", "v10", ["type:build", "comp:common"],
 "Move every service onto one skeleton: config load, lifecycle, one business thread fed by a queue from the I/O side, health, logging.",
 "The common foundation every backend program in the reference is built on.", ["Shared foundation (common libs)"], STD, ["#65", "#66"])

COVERAGE = """Every box of the reference one-chart architecture, mapped to the issue(s) that build its Laddrix version. This issue stays open until every line is ticked; then Laddrix has the full architecture in mock form.

### Trader side
- [ ] Widgets: order ticket, order list — #42, #47
- [ ] Widgets: ladder — #51 · positions & P&L — #57 · algo control — #63
- [ ] Widgets: audit trail, hedge manager — #110 · multi-tab workspace — #111
- [ ] Live connection client — #41, #42 · broadcaster client — #97
- [ ] Office gate / load balancer — #113

### Front end
- [ ] Connection layer — #41, #113 · auth & sessions — #95 · permission check — #95, #94
- [ ] Widget request router — #95 · routing table to sites — #74
- [ ] Order-update fan-in — #41 · market-data fan-out — #50, #51
- [ ] Positions copy and comparison — #55
- [ ] Broadcaster server + data adapter — #97
- [ ] Settings/cache servers — #98 · admin panel & dashboard — #96

### Guard (risk & orders), per site
- [ ] Order intake — #40 · static validation — #44
- [ ] Risk limits — #45, #82 · position reserve — #80
- [ ] Order books (manual, algo, spread, scripted) — #40, #59, #62, #107
- [ ] Order state machine + ID mapping — #29, #22, #40, #46
- [ ] Round robin across runners — #78
- [ ] Execution handling & outbound updates — #40
- [ ] Reconcile with exchange — #79 · persistence & restart — #56
- [ ] Expired contracts — #81 · guard-to-guard link — #83

### Runner, per site
- [ ] Intake & publisher — #39 · session pool — #75 · throttle — #76
- [ ] Encoder / decoder — #39, #38 · disconnect handling — #77
- [ ] Practice runner + simulator — #102
- [ ] Participant ID registration — #84 · queue-position estimate — #103
- [ ] FIX gateway + FIX risk adapter — #104 · broker route — #105

### Newsroom (market data), per site
- [ ] Feed adapters — #49, #100 · gap detection & recovery — #101
- [ ] Instrument & product state, handlers, subscriptions, throttler, publisher — #50
- [ ] Data store + cache — #87 · spread pricing — #61

### Robots
- [ ] Algo server — #59, #60 · spreader — #62, #109
- [ ] Scripted strategies + sandbox + loop guard — #107 · upload tool — #108
- [ ] SDK client algo app — #106

### Exchanges and outside world
- [ ] Mock exchange: book, matching, network — #37, #38 · replay — #49 · fill model — #52
- [ ] Market data feed — #49, #100 · drop copy — #55 · cancel-on-disconnect — #77
- [ ] Second exchange at a second site — #73 · mock broker — #105

### After the trade (accounts office)
- [ ] Event log (careful lane) — #88 · drop copy readers — #90
- [ ] Repartition → fills filter → net position → adjustments → P&L — #54, #89
- [ ] Firm order book copy + API — #91 · compliance alerts — #92
- [ ] Reference data service — #86 · end-of-day jobs — #93 · user setup — #94

### Building and operations
- [ ] Shared service skeleton — #118
- [ ] Metrics + dashboards — #66, #67 · log search — #117 · status monitor — #116
- [ ] Two site groups with fake distance — #68 · environments — #114
- [ ] Deployment process — #115 · failure drills & runbooks — #69, #70
"""
