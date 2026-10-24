"""MockTrade Kite track: milestones and code-first issues, in build order."""

DESIGN = "docs/design/kite-architecture.md"
FOOTER = (
    "\n---\n**How to work:** say **\"start #N\"** and Claude gives the professional code and tests for this "
    "issue, traced line by line with real data. You type it in, run `pytest` and `ruff check .`, then branch "
    "`N-short-name` → PR with `Closes #N` → say \"review #N\" → merge when CI is green. "
    f"Design reference: `{DESIGN}`."
)


def body(goal, idea, steps, tests, done, depends=None, limits=None):
    out = [f"**Goal:** {goal}", f"\n**The idea:** {idea}"]
    if limits:
        out.append(f"\n**Kite / SEBI limits that matter here:** {limits}")
    out.append("\n### Steps")
    out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    out.append("\n### Tests to write first")
    out += [f"- {t}" for t in tests]
    out.append("\n### Done when")
    out += [f"- [ ] {d}" for d in done]
    out.append("- [ ] `pytest` and `ruff check .` pass locally and in CI")
    if depends:
        out.append(f"\n**Depends on:** {depends}")
    return "\n".join(out) + FOOTER


MILESTONES = [
    ("K0 · Foundations", "Python package, money in paise, instruments, order types, broker interface. Pure logic, no network."),
    ("K1 · Live prices", "Kite login, instrument list, binary tick decoder, WebSocket client, market data state. Ends with live NSE prices in your terminal."),
    ("K2 · Paper broker", "Fake-money broker that fills orders against live Kite prices and depth."),
    ("K3 · Orders & risk", "Order manager, throttle under Kite/SEBI limits, risk checks, kill switch, event log, real Kite broker adapter."),
    ("K4 · Positions & P&L", "Positions and P&L from fills, marked to live prices; reconciliation with Kite."),
    ("K5 · Server & screens", "FastAPI server with password + TOTP login, browser WebSocket, React + TypeScript screens: watchlist, ladder, blotter, positions, audit."),
    ("K6 · Algos", "Parent/child algo engine with start/pause/stop, slicer and two-leg spread strategies, algo control screen."),
    ("K7 · Deploy & operate", "Docker Compose + Caddy on a Mumbai server with static IP, latency measurement, morning routine, backups, runbooks."),
]

LABELS = [
    ("track: kite", "0e8a16", "Kite Connect track (active)"),
    ("paused: cme-track", "d4c5f9", "COMEX/MockCME track, paused 14 Oct 2026"),
]

I = []  # (milestone, labels, title, body)

# ---------------- K0 ----------------
K0 = "K0 · Foundations"
I.append((K0, ["type:setup", "comp:common"], "Python package skeleton for the Kite track", body(
    "Turn the repo into a proper Python package (`src/mocktrade`) with tests and a code-style checker running in CI.",
    "Professional Python projects keep code in `src/<name>/`, configure everything in one `pyproject.toml`, and let a linter (**ruff**) catch style and bug patterns automatically, so reviews focus on logic.",
    [
        "Delete the old placeholder folders from the COMEX design: `services/`, `common/` (the `docs/` history stays).",
        "Add `pyproject.toml` (project name, Python ≥ 3.12, pytest and ruff settings).",
        "Add `src/mocktrade/__init__.py` with `__version__` and `src/mocktrade/domain/__init__.py`.",
        "`requirements-dev.txt`: `pytest` and `ruff`, one per line, ending with a newline.",
        "Rewrite `tests/test_smoke.py` to import `mocktrade` (4-space indentation).",
        "CI: add a `ruff check .` step before `pytest`.",
        "README: one paragraph describing the Kite track, linking the design doc.",
    ],
    ["`test_package_imports`: `mocktrade.__version__` is set"],
    ["Old placeholder folders gone", "CI runs ruff and pytest"],
)))
I.append((K0, ["type:build", "comp:common"], "Money in paise and instruments with tick and lot rules", body(
    "Represent every price as whole paise and describe instruments with the tick-size and lot-size rules the exchange enforces.",
    "Kite already sends prices as whole paise (₹2501.05 → 250105). Keeping ints everywhere avoids float errors (same lesson as MockCME #4). An instrument knows its own rules, so a bad price or quantity is caught before it ever reaches Kite.",
    [
        "`src/mocktrade/domain/money.py`: `to_paise(rupees)` and `to_rupees(paise)`.",
        "`src/mocktrade/domain/instruments.py`: enums `Exchange` (NSE, BSE, NFO, BFO) and `InstrumentKind` (EQ, FUT, CE, PE) using Kite's codes as values; frozen dataclass `Instrument` (token, exchange, symbol, kind, tick_paise, lot_size, expiry, strike_paise).",
        "Methods `check_price(price_paise)` and `check_quantity(qty)` that raise `ValueError` with a clear message.",
    ],
    [
        "`to_paise(\"2501.05\") == 250105`, and back with `to_rupees`",
        "`\"2501.055\"`, `\"abc\"` and `\"\"` raise `ValueError` (parametrized)",
        "Prices on the tick grid pass; off-grid, zero and negative prices raise",
        "Whole lots pass; 100 for a lot of 75, zero and negative quantities raise",
        "An `Instrument` can't be changed after creation",
    ],
    ["15 tests pass (including smoke)"],
    depends="#K0.1",
)))
I.append((K0, ["type:build", "comp:rom"], "Order types, order requests and order events", body(
    "Define what an order request looks like and the events that describe an order's life, using Kite's vocabulary.",
    "Every module (risk, brokers, algos, UI) talks about orders. One shared set of types means a Kite order and a paper order look identical to the rest of the system.",
    [
        "`domain/orders.py`: enums `Side` (BUY/SELL), `OrderType` (MARKET, LIMIT, SL, SL-M), `Product` (CNC, MIS, NRML), `Validity` (DAY, IOC).",
        "Frozen `OrderRequest`: client order id (≤ 20 chars, goes in Kite's `tag`), instrument, side, qty, order type, price, trigger price, product, validity, `market_protection`.",
        "`OrderRequest.validate()`: LIMIT needs a price; SL needs price + trigger; MARKET and SL-M need `market_protection` of -1 or 1–100 (never 0); price and quantity pass the instrument's checks.",
        "`OrderStatus` (PENDING, OPEN, PARTIAL, FILLED, CANCELLED, REJECTED) and frozen event `OrderUpdate` (client id, broker order id, status, filled qty, average price, last fill qty/price, reason, timestamp).",
    ],
    [
        "A valid limit order passes `validate()`",
        "MARKET with `market_protection=0` is rejected (the April 2026 rule)",
        "A client order id longer than 20 characters is rejected",
        "SL without a trigger price is rejected",
    ],
    ["Types documented with Kite's matching field names in comments"],
    depends="#K0.2",
    limits="`market_protection` must be -1 or 1–100 for MARKET and SL-M; `tag` max 20 characters.",
)))
I.append((K0, ["type:build", "comp:sor"], "Broker interface and a fake broker for tests", body(
    "Draw the broker adapter boundary: one interface that Kite, Paper and a test Fake all follow.",
    "A Python `Protocol` describes the methods a broker must have. Everything above it is written against the Protocol, so swapping Kite for Paper is a one-line setting, and tests use a Fake that replies however the test needs.",
    [
        "`broker/base.py`: `class Broker(Protocol)` with `async place(request) -> str`, `async modify(...)`, `async cancel(...)`, `updates() -> AsyncIterator[OrderUpdate]`, `async orders()`, `async positions()`.",
        "`broker/fake.py`: `FakeBroker` that records calls and lets a test push scripted `OrderUpdate`s.",
        "A short docstring on each method saying what Kite and Paper must guarantee (e.g. every placed order eventually reaches a final status).",
    ],
    [
        "`FakeBroker` passes an `isinstance(..., Broker)` runtime check (`@runtime_checkable`)",
        "`place` returns a broker order id and records the request",
        "Scripted updates come out of `updates()` in order (first async test, using `asyncio.run`)",
    ],
    ["The interface matches §5 of the design doc"],
    depends="#K0.3",
)))

# ---------------- K1 ----------------
K1 = "K1 · Live prices"
I.append((K1, ["type:build", "comp:sor"], "Kite login: request token to access token", body(
    "Log in to Kite properly each morning and keep the access token safe.",
    "Kite login is a 3-step handshake: you log in on Zerodha's page → Kite redirects to our URL with a short-lived `request_token` → our server exchanges it (with a SHA-256 checksum of api_key + request_token + api_secret) for an `access_token` that works until 6 AM the next day.",
    [
        "Create the Kite Connect app (₹500/month plan) at developers.kite.trade; set the redirect URL; store `KITE_API_KEY` and `KITE_API_SECRET` in a local `.env` (git-ignored).",
        "`broker/kite_session.py`: build the login URL; `checksum(api_key, request_token, api_secret)`; exchange the token using the official `kiteconnect` SDK.",
        "A tiny command `python -m mocktrade.tools.login` that prints the login URL, waits for you to paste the redirected URL, and saves the token to a git-ignored file.",
        "Never log the secret or the token.",
    ],
    [
        "`checksum` matches a hand-computed SHA-256 for a known example",
        "`request_token` is extracted correctly from a pasted redirect URL",
        "Missing env variables give a clear error, not a crash deep inside",
    ],
    ["You logged in once and called `profile()` successfully", "`.env` and the token file are in `.gitignore`"],
    depends="#K0.4",
    limits="Token expires 6 AM next day; login must be done by you (no automated password/TOTP login).",
)))
I.append((K1, ["type:build", "comp:common"], "Instrument list: load Kite's daily instrument dump", body(
    "Turn Kite's daily instrument CSV into `Instrument` objects we can look up by token or symbol.",
    "Kite publishes one CSV of all instruments each day (~08:30). We load it once in the morning and store it; every other module asks the instrument store, never Kite.",
    [
        "`refdata/loader.py`: parse the CSV (columns `instrument_token, tradingsymbol, tick_size, lot_size, instrument_type, segment, exchange, expiry, strike`) into `Instrument`s, converting tick size and strike to paise.",
        "`InstrumentStore` with `by_token(token)` and `by_symbol(exchange, symbol)`.",
        "Skip segments we don't trade (currency, commodities) with a count in the log.",
    ],
    ["A 10-line sample CSV in `tests/data/` loads into the right Instruments", "Tick size `0.05` becomes `5` paise", "Unknown symbol lookup returns `None`"],
    ["Loading the real dump works (`python -m mocktrade.tools.instruments`)"],
    depends="#K1.1",
)))
I.append((K1, ["type:build", "comp:mds"], "Binary tick decoder (ltp, quote, full + 5-level depth)", body(
    "Decode Kite's binary WebSocket frames into `Tick` objects, prices in paise.",
    "Kite packs ticks as bytes to save bandwidth: a frame starts with the packet count, each packet with its length; numbers are big-endian ints; prices are paise. Packet length tells the mode: 8 = ltp, 44 = quote, 184 = full (with 5 bid + 5 ask levels); indices are 28/32.",
    [
        "`domain/ticks.py`: frozen `DepthLevel` and `Tick` (token, ltp, volume, OHLC, depth, timestamps).",
        "`marketdata/decoder.py`: `split_packets(frame)` and `decode_packet(packet)` using `struct` with `>` (big-endian).",
        "Treat 1-byte frames as heartbeats (ignore).",
    ],
    [
        "A hand-built 184-byte packet decodes to the exact numbers put in",
        "Our decoder gives the same values as the official SDK's parser on the same bytes (equivalence test)",
        "A frame with 2 packets gives 2 ticks; a 1-byte heartbeat gives none",
        "Benchmark: decoding 100,000 full packets takes well under a second (prints the time)",
    ],
    ["Decoder handles ltp, quote, full and index packets"],
    depends="#K1.2",
    limits="Full mode = 184 bytes with 5 bid + 5 ask; ~1 tick/second per instrument.",
)))
I.append((K1, ["type:build", "comp:mds"], "Kite WebSocket client: connect, subscribe, reconnect", body(
    "Keep a live WebSocket to Kite that survives network blips and resubscribes automatically.",
    "Real networks drop. A professional client reconnects with growing waits (backoff: 1 s, 2 s, 4 s … max 30 s), resubscribes everything, and raises an alarm if no data or heartbeat arrives for too long.",
    [
        "`marketdata/kite_ws.py` using the `websockets` library: connect to `wss://ws.kite.trade?api_key=…&access_token=…`.",
        "Subscribe/mode messages as JSON (`{\"a\": \"subscribe\", \"v\": [tokens]}`, `{\"a\": \"mode\", \"v\": [\"full\", [tokens]]}`).",
        "Binary frames → decoder → callback; text frames of type `order` → order-update callback.",
        "Reconnect with backoff; a watchdog that reconnects if nothing arrives for 10 s.",
        "Refuse to subscribe more than 3000 tokens per connection.",
    ],
    ["Against a local fake WebSocket server: subscribe message sent on connect", "After the fake server closes, the client reconnects and resubscribes", "A text `order` message reaches the order callback"],
    ["Live run prints NIFTY 50 index ticks in your terminal for 1 minute"],
    depends="#K1.3",
    limits="3000 instruments per connection, 3 connections per API key.",
)))
I.append((K1, ["type:build", "comp:mds"], "Market data service: latest state per instrument and subscribers", body(
    "Hold the newest tick per instrument and let other modules subscribe to changes.",
    "Prices are state, not history: we keep only the latest tick per instrument. Subscribers (screens, paper broker, algos) are notified; slow subscribers get the newest state, never a growing backlog.",
    [
        "`marketdata/service.py`: `MarketData` with `on_tick(tick)`, `latest(token)`, `subscribe(token, callback)`.",
        "A conflating queue per subscriber: if updates arrive faster than it reads, older unread ones are replaced.",
        "`python -m mocktrade.tools.watch NIFTY26OCTFUT` shows a live 5-level ladder in the terminal.",
    ],
    ["`latest` returns the newest tick", "A slow subscriber receives only the newest of 3 quick ticks", "Unsubscribed instruments don't notify anyone"],
    ["Live terminal ladder works during market hours"],
    depends="#K1.4",
)))

# ---------------- K2 ----------------
K2 = "K2 · Paper broker"
I.append((K2, ["type:build", "comp:sor"], "Paper broker: marketable orders fill against live depth", body(
    "A fake-money broker that fills orders that cross the market immediately, walking the 5 depth levels.",
    "A buy at or above the best ask trades now, at each ask level's price, up to the quantity shown there. Whatever can't fill rests (or is cancelled if IOC). MARKET orders become limits at LTP ± market_protection %.",
    ["`broker/paper.py`: `PaperBroker` implementing `Broker`, fed by `MarketData`.", "Fill walk over depth levels; emit `OrderUpdate`s exactly like Kite would.", "Same validation and throttle as the real broker (shared code)."],
    ["Buy 300 with asks 100@A, 150@B, 500@C fills 100@A, 150@B, 50@C", "IOC remainder is cancelled", "MARKET with protection 2% never fills beyond LTP+2%"],
    ["Paper fills look like Kite updates to the rest of the system"],
    depends="#K1.5",
)))
I.append((K2, ["type:build", "comp:sor"], "Paper broker: resting orders and queue position", body(
    "Orders that don't cross wait at their price and fill realistically as the market trades.",
    "When an order rests at a price that has quantity Q ahead of it, traded volume at that price first eats Q, then fills us. If the price trades through our level we fill completely. Conservative on purpose.",
    ["Track queue-ahead per resting order from depth at placement.", "On each tick use volume change and LTP to advance the queue or fill.", "Cancel and modify (price change = back of the queue)."],
    ["Resting buy fills only after enough volume trades at its price", "Price trading through fills fully", "Modify price loses queue position"],
    ["A day of paper trading on NIFTY futures runs without errors"],
    depends="#K2.1",
)))
I.append((K2, ["type:build", "comp:sor"], "Paper broker: simulated latency and rejects", body(
    "Make paper trading feel like the real thing: delays and the same rejections Kite would give.",
    "Acks and fills arrive after a configurable delay (default 80 ms). Orders breaking Kite's rules (tick, lot, market_protection 0, 25-modification cap) are rejected with Kite-like reasons.",
    ["Configurable latency via `asyncio.sleep` in the paper broker.", "Kite rule checks shared with the Kite adapter.", "Modification counter per order (cap 25)."],
    ["26th modification is rejected", "Acks arrive no earlier than the configured latency"],
    ["Paper and Kite share one validation module"],
    depends="#K2.2",
    limits="25 modifications per order.",
)))

# ---------------- K3 ----------------
K3 = "K3 · Orders & risk"
I.append((K3, ["type:build", "comp:rom"], "Order manager: order states and id mapping", body(
    "One place that knows every order's current state and maps our ids to the broker's.",
    "Orders move PENDING → OPEN → PARTIAL → FILLED (or CANCELLED / REJECTED). Updates can arrive out of order or twice; the state machine only moves forward and ignores duplicates.",
    ["`orders/manager.py`: create client ids, send through risk → broker, apply `OrderUpdate`s.", "Allowed transitions table; illegal ones are logged, not applied.", "Lookups by client id and broker id."],
    ["Full life: PENDING → OPEN → PARTIAL → FILLED", "A duplicate update changes nothing", "A late OPEN after FILLED is ignored"],
    ["Works with FakeBroker and PaperBroker"],
    depends="#K2.3",
)))
I.append((K3, ["type:build", "comp:rom"], "Throttle: stay under Kite and SEBI order limits", body(
    "Never send orders faster than the rules allow, with a safety margin.",
    "A **token bucket**: a bucket refills at N tokens per second; each order takes one; empty bucket = wait or refuse. We use three buckets: 8/s, 350/min, 4500/day (under Kite's 10/s, 400/min, 5000/day and SEBI's 10 OPS).",
    ["`risk/throttle.py`: `TokenBucket` with an injected clock.", "`OrderThrottle` combining the three buckets; algos wait, manual clicks are refused with a clear message when full."],
    ["9th order within one second is held back", "Bucket refills over time (fake clock)", "Daily cap blocks order 4501"],
    ["Limits configurable, defaults below Kite/SEBI caps"],
    depends="#K3.1",
    limits="Kite: 10/s, 400/min, 5000/day per user. SEBI: >10 OPS needs exchange registration.",
)))
I.append((K3, ["type:build", "comp:rom"], "Pre-trade risk checks and kill switch", body(
    "Every order passes your own limits before it can leave: the first line of defence.",
    "Checks: allowed instruments, max quantity per order, max order value, max position per instrument, price within X% of LTP (fat-finger), daily loss limit. A kill switch cancels everything and blocks new orders.",
    ["`risk/checks.py`: each check is a small function returning a reason or None.", "`RiskLimits` loaded from config.", "Kill switch: cancel all working orders, block new ones until reset."],
    ["Order 20% away from LTP is rejected", "Order that would exceed max position is rejected", "Kill switch cancels working orders and blocks new ones"],
    ["Every rejection has a human-readable reason"],
    depends="#K3.2",
)))
I.append((K3, ["type:build", "comp:accounts"], "Event log: the audit trail in SQLite", body(
    "Record every order event, append-only, so any order's full history can be shown and nothing is lost on restart.",
    "SQLite in WAL mode is a crash-safe file database. One writer task appends events; screens read the history. On restart, the order manager rebuilds working orders from the log, then reconciles with the broker.",
    ["`store/events.py`: table `events(id, ts, kind, client_order_id, payload_json)`.", "Single writer via an asyncio queue.", "`history(client_order_id)` for the audit screen."],
    ["Events come back in the order written", "Data survives closing and reopening the database", "Rebuild working orders from the log"],
    ["Every state change of every order is in the log"],
    depends="#K3.3",
)))
I.append((K3, ["type:build", "comp:sor"], "Kite broker adapter: real orders and reconciliation", body(
    "Place, modify and cancel real orders through Kite behind the same `Broker` interface.",
    "REST calls go through the official SDK in a worker thread (`asyncio.to_thread`); order updates come from WebSocket `order` messages; after any reconnect we call `orders()` and reconcile differences.",
    ["`broker/kite.py`: `KiteBroker` mapping our `OrderRequest` to Kite parameters (variety, product, validity, `market_protection`, `tag`).", "Map Kite statuses (OPEN, COMPLETE, CANCELLED, REJECTED, interim statuses) to ours.", "Reconcile on reconnect and at startup."],
    ["Request → Kite parameters mapping (no network, SDK mocked)", "Kite order message → our `OrderUpdate`", "Reconcile finds an order filled while we were disconnected"],
    ["First real order placed as an **AMO** (after-market order) from the server with the registered static IP, then cancelled"],
    depends="#K3.4",
    limits="Order endpoints only accept the registered static IP; tag ≤ 20 chars; market_protection ≠ 0.",
)))

# ---------------- K4 ----------------
K4 = "K4 · Positions & P&L"
I.append((K4, ["type:build", "comp:accounts"], "Positions and P&L from fills", body(
    "Compute net position, average price, realised and unrealised P&L per instrument from fills and live prices.",
    "Position = sum of signed fill quantities. Realised P&L comes from closing trades; unrealised = (LTP − average price) × position. All in paise.",
    ["`positions/book.py`: apply fills; mark to market on ticks.", "Totals per day."],
    ["Buy 75 @ 100, sell 75 @ 102 → realised +150 rupees", "Unrealised follows LTP", "Flip from long to short handled"],
    ["P&L numbers match a hand calculation"],
    depends="#K3.5",
)))
I.append((K4, ["type:build", "comp:accounts"], "Reconcile positions with Kite", body(
    "Compare our positions with Kite's every few minutes and flag any difference.",
    "Our numbers come from our fills; Kite's from the exchange. They must agree. A difference means a missed update and must be visible immediately.",
    ["Periodic `positions()` call (respecting 10 req/s).", "Difference → alert in the UI + event log."],
    ["A missing fill shows up as a difference", "Equal positions produce no alert"],
    ["Alert visible on the positions screen"],
    depends="#K4.1",
)))

# ---------------- K5 ----------------
K5 = "K5 · Server & screens"
I.append((K5, ["type:build", "comp:appserver"], "Server: FastAPI app with password + TOTP login", body(
    "One web server that only you can use: it serves the UI and the API, behind password and 2FA.",
    "Our server can place real orders, so it needs strong locks (SEBI's \"no open APIs\" spirit). FastAPI gives typed REST endpoints; sessions use secure cookies.",
    ["`server/app.py` (FastAPI), `server/auth.py` (bcrypt password + pyotp TOTP).", "Endpoints: login, place/cancel order, orders, positions, audit history.", "Kite login callback endpoint (completes #K1.1 in the browser)."],
    ["Endpoints refuse requests without a valid session", "Wrong TOTP is rejected", "Place order goes through the order manager"],
    ["Login works from your phone"],
    depends="#K4.2",
)))
I.append((K5, ["type:build", "comp:appserver"], "Browser WebSocket with conflation", body(
    "Push prices, orders and positions to the browser as they change, without flooding it.",
    "One WebSocket per browser tab; the server sends JSON messages; per instrument at most ~4 updates per second (newest wins).",
    ["`server/stream.py`: subscribe/unsubscribe messages from the browser.", "Conflation timer (250 ms)."],
    ["3 ticks within 250 ms → 1 message with the newest data", "Order updates are never conflated (every one is sent)"],
    ["Message formats documented in `docs/design/`"],
    depends="#K5.1",
)))
I.append((K5, ["type:build", "comp:ui"], "UI skeleton: React + TypeScript, login and watchlist", body(
    "Create the browser app and show a live watchlist.",
    "Vite creates a React + TypeScript project; TypeScript catches type mistakes before the browser runs the code.",
    ["`ui/` with Vite (React + TS).", "Login page, then a watchlist of instruments with LTP and change.", "Dev proxy to the Python server."],
    ["Component test for the watchlist row (Vitest)"],
    ["Live watchlist updates in the browser"],
    depends="#K5.2",
)))
I.append((K5, ["type:build", "comp:ui"], "Ladder: 5-level depth, working orders, click to trade", body(
    "The trader's main screen: a price ladder with market depth and your working orders; click to buy or sell.",
    "Rows are prices around LTP on the instrument's tick grid; bid/ask quantities from 5-level depth; your working orders shown on their price rows. A banner states depth is 5 levels, ~1 update/second.",
    ["Ladder component; click left = buy at that price, right = sell; quantity selector in lots.", "Cancel by clicking your order; confirmation setting."],
    ["Rows align to tick size", "Clicking builds the right order request"],
    ["Paper trading from the ladder works end to end"],
    depends="#K5.3",
)))
I.append((K5, ["type:build", "comp:ui"], "Blotter, positions and audit screens", body(
    "See all orders, live positions/P&L, and the full history of any order.",
    "These read from the order manager, positions book and event log through the server.",
    ["Order blotter with status filter and cancel buttons.", "Positions with realised/unrealised P&L and reconciliation alerts.", "Audit view: click an order → every event with timestamps."],
    ["Status filter works", "Audit view shows events in order"],
    ["All screens update live"],
    depends="#K5.4",
)))

# ---------------- K6 ----------------
K6 = "K6 · Algos"
I.append((K6, ["type:build", "comp:algos"], "Algo engine: parent and child orders", body(
    "Run strategies that manage a parent order by sending child orders, with start, pause and stop.",
    "A parent order is the trader's intent (\"buy 750 over 10 minutes\"); the algo sends child orders to the order manager. Every child goes through the same risk and throttle as manual orders.",
    ["`algos/engine.py`: algo lifecycle, parent ↔ child links, timers via injected clock.", "Stopping an algo cancels its working children."],
    ["Stop cancels all children", "Children respect the throttle", "Parent filled qty = sum of children fills"],
    ["Engine runs against the paper broker"],
    depends="#K5.5",
)))
I.append((K6, ["type:build", "comp:algos"], "Slicer algo (time-sliced orders)", body(
    "Split a big order into small limit orders over time.",
    "Every interval, send one slice at the best price on our side; if it hasn't filled by the next slice, modify (counting modifications; at 24 cancel and replace).",
    ["`algos/slicer.py` with parameters: total qty, slices, interval, price limit."],
    ["10 slices over 10 minutes with a fake clock", "Respects the 25-modification cap"],
    ["Paper run completes a 750-lot parent"],
    depends="#K6.1",
    limits="25 modifications per order; ≤ 8 orders/s via the throttle.",
)))
I.append((K6, ["type:build", "comp:algos"], "Two-leg spread algo (legging)", body(
    "Trade a spread (e.g. buy near-month future, sell next-month) as one instruction.",
    "Quote one leg; when it fills, immediately hedge the other leg within a pay-up limit; track legging risk (time and price between the two fills).",
    ["`algos/spread.py`: synthetic spread price from both legs' live prices; quoting leg + hedge leg."],
    ["Hedge sent right after the quoting leg fills", "Pay-up limit respected", "Stop leaves no unhedged position without an alert"],
    ["Paper run on a NIFTY calendar spread"],
    depends="#K6.2",
)))
I.append((K6, ["type:build", "comp:ui"], "Algo control screen", body(
    "Start, pause and stop algos from the browser and see parent/child progress.",
    "Parent row with progress bar; expand to see children; buttons for pause/resume/stop.",
    ["UI component + server endpoints for algo control."],
    ["Stop button calls the engine and children disappear"],
    ["Algos fully controllable from the browser"],
    depends="#K6.3",
)))

# ---------------- K7 ----------------
K7 = "K7 · Deploy & operate"
I.append((K7, ["type:build", "comp:deploy"], "Docker Compose and Caddy", body(
    "Package the app so it runs the same on your laptop and the server, behind HTTPS.",
    "Docker puts the app and its exact Python version in a box; Compose starts the boxes together; Caddy gets HTTPS certificates automatically.",
    ["`Dockerfile` for the app (multi-stage: build UI, then Python).", "`deploy/compose.yaml` with app + Caddy; `deploy/Caddyfile`.", "Secrets from `.env`, data in a volume."],
    ["`docker compose up` starts both and `/health` answers"],
    ["Runs locally in paper mode"],
    depends="#K6.4",
)))
I.append((K7, ["type:setup", "comp:deploy"], "Mumbai server, static IP and hardening", body(
    "Run MockTrade on AWS Lightsail (Mumbai) with a static IP registered in Kite.",
    "Real orders must come from the registered IP. The server is reachable only on 443 (and 22 with SSH keys).",
    ["Create Lightsail 2 GB instance in Mumbai; attach a static IP.", "Register the IP in the Kite developer console (IP Whitelist).", "Firewall, SSH keys only, automatic security updates, daily SQLite backup."],
    ["Checklist in `docs/runbooks/server.md`"],
    ["AMO test order from the server succeeds; from the laptop it's refused"],
    depends="#K7.1",
    limits="Order endpoints accept only the registered static IP (1 primary + 1 backup).",
)))
I.append((K7, ["type:test", "comp:deploy"], "Measure latency and load", body(
    "Replace estimates with measurements: our internal latency, Kite's round trip, CPU at full subscription.",
    "Measure-first: record timestamps at each step, keep histograms, look at p50/p99. Only optimise what the numbers show.",
    ["Timestamps: tick received → decoded → sent to browser; click → request sent → Kite ack.", "`/metrics` endpoint; a load test subscribing 1000 instruments."],
    ["Histogram math tested with known samples"],
    ["Numbers written into the design doc's latency table"],
    depends="#K7.2",
)))
I.append((K7, ["type:build", "comp:deploy"], "Morning routine, alerts and runbooks", body(
    "Make daily operation boring: instrument refresh, login reminder, health checks, and what to do when things break.",
    "Runbooks are step-by-step fixes written before you need them.",
    ["08:30 instrument refresh job; login reminder until the Kite token is fresh.", "Runbooks: login failed, WebSocket down, reconciliation mismatch, kill switch."],
    ["Scheduler runs jobs at the right times with a fake clock"],
    ["A full trading day runs with no manual fixes"],
    depends="#K7.3",
)))

if __name__ == "__main__":
    print(len(I))
    for n, (m, l, t, b) in enumerate(I, 1):
        print(n, m, "|", t)
