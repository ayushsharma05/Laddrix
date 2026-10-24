"""MockCME backlog: milestones, labels and issues. Issue numbers start at #1 in order."""

FOOTER = ("\n---\n**How to work:** branch `N-short-name` from `main` → write the tests first and watch them fail → "
          "write code until they pass → small commits → PR with `Closes #N` → ask Claude to review → merge when CI is green.")


def body(goal, idea=None, build=None, tests=None, spec=None, done=None, depends=None, note=None):
    out = [f"**Goal:** {goal}"]
    if idea:
        out.append(f"\n**The idea in 30 seconds:** {idea}")
    if build:
        out.append("\n### Build")
        out += [f"{i}. {s}" for i, s in enumerate(build, 1)]
    if tests:
        out.append("\n### Tests to write first")
        out += [t if t.startswith("```") else f"- {t}" for t in tests]
    out.append("\n### Done when")
    out += [f"- [ ] {d}" for d in (done or [])]
    out.append("- [ ] `pytest` passes and CI is green")
    if spec:
        out.append(f"- [ ] {spec}")
    if depends:
        out.append(f"\n**Depends on:** {depends}")
    if note:
        out.append(f"\n{note}")
    return "\n".join(out) + FOOTER


MILESTONES = [
    ("E0 · Setup", "Repo, laptop setup, design review and project board."),
    ("E1 · Matching core", "Instruments, orders, books, matching, cancel/modify, time in force, trading states. Pure logic, no network. Ends with tag v0.1.0."),
    ("E2 · Order entry", "iLink-lite: TCP gateway, sessions, heartbeats, a command-line trading client. Ends with tag v0.2.0."),
    ("E3 · Market data", "MDP-lite: level updates, numbered UDP packets, trades, definitions, status, snapshots, a listener. Ends with tag v0.3.0."),
    ("E4 · Real data replay", "Replay a real COMEX day and fill your orders against it. Ends with tag v0.4.0."),
    ("E5 · Safety & recovery", "Cancel on disconnect, throttle, banding, self-match prevention, mass cancel, journal and restart, resend."),
    ("E6 · Drop copy", "A second copy of every fill for back offices."),
    ("E7 · Spreads", "Listed calendar spreads with legs, then implied prices."),
    ("E8 · Second venue", "Two exchanges at two sites."),
]

LABELS = [
    ("code", "1d76db", "You write program code"),
    ("docs", "0e8a16", "Writing or reviewing docs"),
    ("setup", "c5def5", "Tools and project setup"),
    ("release", "5319e7", "Test everything and tag a version"),
    ("core", "fbca04", "Matching engine (pure logic)"),
    ("orderentry", "f9d0c4", "Order entry protocol and sessions"),
    ("marketdata", "bfdadc", "Market data feed"),
    ("replay", "d4c5f9", "Replay of real data"),
    ("detail-later", "ededed", "Steps get written in detail when you reach it"),
]

I = []  # (milestone, labels, title, body)

# ---------------- E0 ----------------
I.append(("E0 · Setup", ["setup"], "Clone MockCME, set up the venv and run the tests", body(
    "Get MockCME running on your laptop in VS Code.",
    build=[
        "In VS Code: **Clone Git Repository** → `ayushsharma05/MockCME`.",
        "In the terminal: `python -m venv .venv`, then `.venv\\Scripts\\activate`, then `pip install -r requirements-dev.txt`.",
        "Run `pytest`. You should see `1 passed`.",
        "Open `src/mockcme/` and read the one-line description at the top of each `__init__.py`. That's the whole map.",
    ],
    done=["`pytest` shows 1 passed on your laptop"],
    note="No PR needed. Close this issue with a comment saying what you saw.")))

I.append(("E0 · Setup", ["docs"], "Review and merge the design PR", body(
    "Agree on the exchange's architecture and decisions before writing code.",
    idea="`docs/design.md` is one page: a diagram, 8 decisions, 7 rules and the scope. Real teams review this before building.",
    build=[
        "Open the design PR. Read `docs/design.md` on GitHub, where the diagram renders.",
        "Comment on any line you disagree with or don't understand. Asking \"why?\" is a good review comment.",
        "When you're happy, merge it.",
    ],
    done=["Design PR merged"],
    depends="#1")))

I.append(("E0 · Setup", ["setup"], "Create the MockCME project board", body(
    "Get a board where you can see every issue's status at a glance.",
    build=[
        "GitHub → your profile → **Projects** → **New project** → **Board**. Name it `MockCME`.",
        "Add all MockCME issues: in the board, type `#` in the add-item box, or use **Add items** and filter `repo:ayushsharma05/MockCME`.",
        "Columns: Todo, In progress, Done. Turn on the built-in workflows **Item closed → Done** and **Pull request merged → Done**.",
        "Link the project to the repo: repo → **Projects** tab → **Link a project**.",
    ],
    done=["Board exists, has every issue, and closed issues move to Done automatically"],
    note="You did the same for MockTrade in its issue #4.")))

# ---------------- E1 ----------------
E1 = "E1 · Matching core"
I.append((E1, ["code", "core"], "Instruments and prices in whole ticks", body(
    "Describe the gold, silver and copper contracts in code, and convert prices to whole ticks and back.",
    idea="A tick is the smallest price step. Gold moves in 0.1 steps, so 2400.5 is 24005 ticks. Inside the exchange every price is a whole number of ticks (an `int`), and we only convert at the edges. Use `Decimal` for the conversion, because `float` can't store 0.1 exactly.",
    build=[
        "File `src/mockcme/core/instruments.py`. A `@dataclass(frozen=True)` called `Instrument` with fields `symbol` (\"GCZ6\"), `product` (\"GC\"), `tick_size` (`Decimal`), `contract_size` (`int`) and `unit` (\"oz\"). *Frozen* means it can't be changed after it's created.",
        "`to_ticks(instrument, price) -> int`. It accepts a `str` or `Decimal` and raises `ValueError` if the price isn't a whole number of ticks (e.g. gold 2400.55).",
        "`from_ticks(instrument, ticks) -> Decimal`.",
        "`tick_value(instrument) -> Decimal`: money per tick for one contract (tick_size × contract_size).",
        "A dict `INSTRUMENTS` keyed by symbol, holding GCZ6, GCG7 (gold: 0.1, 100 oz), SIZ6, SIH7 (silver: 0.005, 5000 oz) and HGZ6, HGH7 (copper: 0.0005, 25000 lb).",
    ],
    tests=[
        "File `tests/test_instruments.py`. Here's your first test, written out. Copy the pattern for the rest:",
        "```python\nfrom decimal import Decimal\n\nimport pytest\n\nfrom mockcme.core.instruments import INSTRUMENTS, to_ticks\n\n\ndef test_gold_price_to_ticks():\n    gold = INSTRUMENTS[\"GCZ6\"]\n    assert to_ticks(gold, \"2400.5\") == 24005\n```",
        "`from_ticks(gold, 24005) == Decimal(\"2400.5\")` (a round trip)",
        "Gold `\"2400.55\"` raises `ValueError` (use `with pytest.raises(ValueError):`)",
        "Silver `\"30.005\"` → 6001 ticks; copper `\"4.5005\"` → 9001 ticks",
        "Tick values: gold `Decimal(\"10\")`, silver `Decimal(\"25\")`, copper `Decimal(\"12.5\")`",
    ],
    spec="Add 3–4 lines under **Instruments and prices** in `docs/spec/rules.md`",
    done=["All tests above written and passing"],
    depends="#2 (design merged)")))

I.append((E1, ["code", "core"], "Orders: side, time in force and fills", body(
    "Create the `Order` object that every other part of the exchange passes around.",
    idea="An order is a promise to buy or sell. It remembers how much it wanted (`qty`) and how much has traded (`filled`). What's left (`leaves`) is computed, never stored, so the two numbers can't disagree.",
    build=[
        "File `src/mockcme/core/orders.py`. `class Side(Enum)` with `BUY` and `SELL`, plus a property `opposite`.",
        "`class TimeInForce(Enum)` with `DAY`, `GTC` and `FAK`.",
        "`@dataclass class Order` with `order_id: int`, `cl_ord_id: str`, `session: str`, `symbol: str`, `side: Side`, `price: int` (ticks), `qty: int`, `tif: TimeInForce` and `filled: int = 0`.",
        "Property `leaves` = qty − filled. Property `is_filled`.",
        "Method `fill(qty)` adds to `filled`. It raises `ValueError` if qty ≤ 0 or qty > leaves.",
    ],
    tests=[
        "New order: leaves == qty, not filled",
        "`fill(2)` on a 5-lot → leaves 3; `fill(3)` more → is_filled",
        "Overfill (`fill(6)` on a 5-lot) raises",
        "`Side.BUY.opposite is Side.SELL` and the reverse",
    ],
    depends="#4")))

I.append((E1, ["code", "core"], "The order book: price levels in time order", body(
    "Store resting orders so the best price and the first order in line are always quick to find.",
    idea="Each side of the book is a dict from price to a queue (`collections.deque`) of orders in arrival order. Best bid = highest bid price; best ask = lowest ask price. A second dict, `order_id → order`, finds any order instantly for cancels.",
    build=[
        "File `src/mockcme/core/book.py`. `class OrderBook` with `__init__(self, symbol)`, `self.bids` / `self.asks` (dict price → deque) and `self.orders` (dict order_id → Order).",
        "`add(order)`: append to the queue at its price on its side.",
        "`best(side) -> int | None`: best price on that side, or `None` if it's empty.",
        "`first_at(side, price) -> Order`: the order at the front of the queue.",
        "`remove(order_id) -> Order`: take it out. If the level becomes empty, delete that price.",
        "`get(order_id) -> Order | None`.",
        "`levels(side, depth=10) -> list[tuple[int, int, int]]`: (price, total leaves, number of orders), best first.",
    ],
    tests=[
        "Empty book: `best(BUY)` and `best(SELL)` are `None`",
        "Bids at 24004 and 24005 → best bid 24005. Asks at 24006 and 24007 → best ask 24006",
        "Two orders at the same price → `first_at` returns the one added first",
        "Remove the only order at a price → that level is gone and `best` moves",
        "`levels` adds up quantity and counts orders per price, best first, and stops at `depth`",
    ],
    depends="#5")))

I.append((E1, ["code", "core"], "Matching: price-time priority, partial fills", body(
    "Match an incoming order against the book. This is the heart of the exchange.",
    idea="While the new order has quantity left and the best opposite price is good enough (buy: best ask ≤ my price; sell: best bid ≥ my price), trade with the **first** order at the **best** price. The trade happens at the **resting** order's price. Fill both sides, and remove the resting order once it's fully filled.",
    build=[
        "File `src/mockcme/core/matching.py`. `@dataclass(frozen=True) class Fill` with `aggressor_id`, `resting_id`, `price` and `qty`.",
        "`match(book, incoming: Order) -> list[Fill]`. It does **not** add the leftover to the book. The engine decides that (FAK orders don't rest).",
    ],
    tests=[
        "**Your #14 paper exercise, MockTrade issue #14**: sells 2400.4 × 3 and 2400.5 × 5 rest, then buy 6 @ 2400.5 → fills 3 @ 2400.4 and 3 @ 2400.5. The second seller has 2 left, and the buyer is fully filled.",
        "No cross (buy 2400.3 vs best ask 2400.4) → no fills, book unchanged",
        "Time priority: two sells at 2400.4 (first A, then B), buy 1 → A trades, B untouched",
        "Price improvement goes to the aggressor: buy @ 2401.0 against a sell @ 2400.4 → trade at 2400.4",
        "The mirror case: an incoming sell sweeps two bid levels",
    ],
    spec="Write the matching rules under **Matching** in `docs/spec/rules.md`",
    depends="#6")))

I.append((E1, ["code", "core"], "Engine v1: new orders in, execution reports out", body(
    "One entry point for the whole exchange: `engine.handle(command)` returns a list of execution reports.",
    idea="Commands are what clients ask for. Events are what happened. The engine checks the command, gives the order an official `order_id`, matches it, rests what's left and reports each step. No network here, just a function you can test.",
    build=[
        "File `core/commands.py`: `@dataclass(frozen=True) class NewOrder` with `session`, `cl_ord_id`, `symbol`, `side`, `price` (ticks), `qty` and `tif` (default DAY).",
        "File `core/events.py`: `class ExecType(Enum)` with NEW, TRADE, CANCELED, MODIFIED, REJECTED, EXPIRED, CANCEL_REJECTED and MODIFY_REJECTED. `@dataclass(frozen=True) class ExecReport` with `exec_type`, `session`, `order_id`, `cl_ord_id`, `symbol`, `side`, `price`, `cum_qty`, `leaves_qty`, `last_qty=0`, `last_px=0` and `reason=\"\"`.",
        "File `core/engine.py`: `class Engine` built with the instruments. It keeps one `OrderBook` per symbol and an order-id counter starting at 1.",
        "`handle(cmd) -> list[ExecReport]`. For `NewOrder`: validate (unknown symbol, qty ≤ 0, price ≤ 0, `cl_ord_id` already used by that session → REJECTED with a reason). Otherwise report NEW, match, report **two** TRADE reports per fill (the aggressor's and the resting order's, each to its own session), then rest what's left.",
    ],
    tests=[
        "A valid order → one NEW report with leaves == qty and a fresh order_id",
        "Unknown symbol → REJECTED with reason `\"unknown symbol\"`, nothing added to the book",
        "Duplicate cl_ord_id in the same session → REJECTED. The same cl_ord_id in another session → fine",
        "A crossing order → NEW, then TRADE reports for both sides with the right `last_qty`, `last_px`, `cum_qty` and `leaves_qty`",
        "The resting order's TRADE report goes to the resting order's **session**",
    ],
    spec="Add the execution report types under **Orders** in `docs/spec/rules.md`",
    depends="#7")))

I.append((E1, ["code", "core"], "Cancel and modify, with the queue-priority rules", body(
    "Let traders cancel and change orders, keeping or losing their place in line exactly like CME.",
    idea="Reducing quantity at the same price keeps your place in the queue. Changing the price, or increasing quantity, is like cancelling and re-entering, so you go to the back. A modify to a crossing price can trade right away.",
    build=[
        "`core/commands.py`: `CancelOrder(session, order_id)` and `ModifyOrder(session, order_id, price, qty)`. `qty` is the new **total** quantity, as at CME.",
        "Cancel: unknown order, someone else's order, or already finished → CANCEL_REJECTED with a reason. Otherwise remove it and report CANCELED.",
        "Modify: reject when the new qty ≤ already filled (MODIFY_REJECTED). Qty down at the same price → change in place and keep priority. Otherwise remove and re-add at the back, then run matching.",
        "Report MODIFIED with the new price and leaves.",
    ],
    tests=[
        "Cancel your resting order → CANCELED, book empty",
        "Cancel another session's order → CANCEL_REJECTED, order still there",
        "Cancel after it's fully filled → CANCEL_REJECTED (the race from the CME guide)",
        "Two sells at the same price, A then B. A reduces qty → A still first. A raises qty → B now first",
        "Modify a bid up to the best ask → MODIFIED, then TRADE",
    ],
    spec="Write the priority rules under **Cancel and modify** in `docs/spec/rules.md`",
    depends="#8")))

I.append((E1, ["code", "core"], "Time in force: FAK, Day, GTC and end of day", body(
    "Make orders live exactly as long as the trader asked.",
    idea="FAK fills what it can right now, and the rest dies at once. DAY orders die at the end of the day. GTC orders stay until cancelled. A dead order gets an EXPIRED report so the trader always knows.",
    build=[
        "In `Engine`, after matching a new order or modify: if tif is FAK and leaves > 0 → don't rest it; report EXPIRED with reason `\"FAK\"`.",
        "`Engine.end_of_day() -> list[ExecReport]`: remove every DAY order and report EXPIRED with reason `\"end of day\"`. GTC orders stay.",
    ],
    tests=[
        "FAK buy 5 with 3 available → TRADE for 3, then EXPIRED for 2; nothing rests",
        "FAK with nothing to trade → NEW then EXPIRED",
        "end_of_day with one DAY and one GTC resting → only the DAY order expires",
    ],
    spec="Write the rules under **Time in force** in `docs/spec/rules.md`",
    depends="#9")))

I.append((E1, ["code", "core"], "Trading states: pre-open, open, halt, closed", body(
    "Give each instrument a state that controls what's allowed.",
    idea="PRE_OPEN collects orders but doesn't match. OPEN matches. HALT allows only cancels. CLOSED allows nothing. When PRE_OPEN turns OPEN, any crossed orders trade right away (a simple uncross; the real opening auction comes later).",
    build=[
        "`class TradingState(Enum)` in `core/events.py` (or its own file).",
        "Command `SetState(symbol, state)`. The engine keeps the state per instrument, starting at PRE_OPEN.",
        "New and modify → REJECTED / MODIFY_REJECTED when the state doesn't allow them. Cancel allowed in PRE_OPEN, OPEN and HALT.",
        "In PRE_OPEN, new orders rest without matching.",
        "On PRE_OPEN → OPEN: while best bid ≥ best ask, trade the two first orders at the price of the one that arrived earlier.",
        "On → CLOSED: run `end_of_day()` for that symbol.",
    ],
    tests=[
        "Crossed buy and sell in PRE_OPEN → both rest, no trade. Then OPEN → they trade",
        "HALT: new order rejected, cancel accepted",
        "CLOSED: DAY orders expired, GTC orders kept, new orders rejected",
    ],
    spec="Write the state table under **Trading states** in `docs/spec/rules.md`",
    depends="#10")))

I.append((E1, ["code", "core"], "The big random test: 10,000 orders and the rules still hold", body(
    "Prove the engine never breaks its own rules, using thousands of random orders.",
    idea="Instead of guessing every case, generate random orders, cancels and modifies with a fixed seed, and after **every** step check things that must always be true. These are called **invariants**. The fixed seed means a failure can be replayed exactly.",
    build=[
        "File `tests/test_engine_random.py`. Use `random.Random(42)` to create 10,000 commands across 2 symbols, near a middle price.",
        "After each `handle`, check: the book is never crossed while OPEN (best bid < best ask); every order has 0 ≤ filled ≤ qty; each level's quantity = the sum of its orders' leaves; total bought = total sold for each symbol.",
        "Determinism: run the same seed twice and the lists of reports are identical.",
    ],
    tests=["The test is the build. Keep it under a few seconds."],
    done=["Random test passes with seeds 1, 2 and 42"],
    depends="#11")))

I.append((E1, ["release"], "Release E1: tag v0.1.0", body(
    "Ship the matching core as version 0.1.0.",
    idea="A **tag** is a permanent name for one commit. Later you can always go back to exactly what v0.1.0 was.",
    build=[
        "Create `CHANGELOG.md` with a `## v0.1.0` section: 4–6 lines on what the core can do.",
        "Set `version = \"0.1.0\"` in `pyproject.toml` and `__version__` in `src/mockcme/__init__.py`. Open a PR, then merge it.",
        "On GitHub: **Releases → Draft a new release → tag `v0.1.0`** on main, paste the changelog section and publish.",
    ],
    done=["Release v0.1.0 exists on GitHub"],
    depends="#12")))

# ---------------- E2 ----------------
E2 = "E2 · Order entry"
I.append((E2, ["code", "orderentry"], "Message format: one JSON message per line", body(
    "Define the messages clients and the exchange send each other, and turn them into bytes and back.",
    idea="Over TCP, data is just a stream of bytes, so we need to know where one message ends. Rule: one JSON object per line, ending with `\\n`. Every message has a `type`. Prices travel as text (\"2400.5\") and become ticks at the door using `to_ticks`.",
    build=[
        "File `src/mockcme/orderentry/protocol.py`. `encode(msg: dict) -> bytes` and `decode(line: bytes) -> dict`. Decode raises `ProtocolError` on bad JSON or a missing `type`.",
        "Client → exchange: `Logon`, `Logout`, `Heartbeat`, `NewOrder`, `CancelOrder` and `ModifyOrder`. Exchange → client: `LogonAck`, `ExecReport`, `Heartbeat`, `Reject` and `Logout`. Every message has a `seq` number.",
        "`to_command(msg, session) -> NewOrder | CancelOrder | ModifyOrder`: converts price text to ticks and side/tif text to enums. Raises `ProtocolError` on unknown fields or an off-tick price.",
        "`from_report(report: ExecReport) -> dict`: converts ticks back to price text.",
    ],
    tests=[
        "`decode(encode(m)) == m` for each message type",
        "Bad JSON → `ProtocolError`",
        "NewOrder with price \"2400.55\" for gold → `ProtocolError`",
        "`from_report` shows `\"2400.5\"`, not 24005",
    ],
    spec="Write every message with an example line in `docs/spec/order-entry.md`",
    depends="#13")))

I.append((E2, ["code", "orderentry"], "TCP server: accept clients and read lines", body(
    "Open the exchange's door: a TCP server that many clients can connect to at once.",
    idea="`asyncio` lets one thread handle many connections by waiting on all of them together. `asyncio.start_server` calls your function once per client, with a reader and a writer.",
    build=[
        "File `src/mockcme/orderentry/server.py`. `async def handle_client(reader, writer)`: read lines in a loop; first version: echo each line back.",
        "Then: decode each line. On `ProtocolError` send a `Reject` with the reason instead of crashing.",
        "`async def start(host, port)` returns the server. Port 0 means \"pick any free port\", which is useful in tests.",
    ],
    tests=[
        "In a test, use `asyncio.run(...)` to start the server on port 0, connect with `asyncio.open_connection`, send a line and get it back",
        "Send `not json` → receive a `Reject`, and the connection stays open",
        "Two clients at once each get their own replies",
    ],
    depends="#14")))

I.append((E2, ["code", "orderentry"], "Sessions: logon, sequence numbers, heartbeats", body(
    "Make each connection a trusted session with a name, numbered messages and a heartbeat.",
    idea="The first message must be `Logon` with a session name. Every message after it carries `seq` = previous + 1, in both directions, so a missing message is noticed. If the client goes silent too long, the exchange sends a heartbeat. If the silence continues, it disconnects.",
    build=[
        "File `orderentry/session.py`. `class Session` with states CONNECTED → LOGGED_ON → LOGGED_OUT, `expected_in_seq` and `next_out_seq`.",
        "Anything before Logon → Reject and disconnect. A second login of the same session name → reject it.",
        "Wrong `seq` → `Reject` with reason `\"sequence gap\"`. (Resending comes in E5.)",
        "Heartbeat: interval set at Logon (default 30 s). No message for the interval → send Heartbeat. Silence for 2× the interval → Logout and disconnect. Pass a clock in, so tests don't wait 30 seconds.",
    ],
    tests=[
        "NewOrder before Logon → Reject, connection closed",
        "Logon → LogonAck with seq 1",
        "Skipping from seq 2 to seq 4 → Reject \"sequence gap\"",
        "With a fake clock moved forward 2× the interval → session logged out",
    ],
    spec="Write the session rules in `docs/spec/order-entry.md`",
    depends="#15")))

I.append((E2, ["code", "orderentry"], "Connect the gateway to the engine", body(
    "Orders from any session reach the one matching engine, and every report goes back to the right session.",
    idea="Every session drops its commands into **one** `asyncio.Queue`. One task takes them out one at a time and calls `engine.handle`. That's decision 2 from the design: one line, one engine. Each report has a `session`, so a dict `session name → writer` sends it to the right connection.",
    build=[
        "File `orderentry/gateway.py`. `class Gateway` holds the engine, the queue and the session → writer dict.",
        "Session code: `NewOrder` / `CancelOrder` / `ModifyOrder` → `to_command` → put in the queue.",
        "Engine task: take a command, `handle` it, send each report through `from_report` to its session (if it's still connected).",
    ],
    tests=[
        "Client A logs on and rests a sell. Client B logs on and buys → A gets TRADE, B gets NEW + TRADE",
        "B cancels A's order → B gets CANCEL_REJECTED, A gets nothing",
    ],
    depends="#16")))

I.append((E2, ["code", "orderentry"], "A command-line trading client", body(
    "Trade on your own exchange by typing, like a tiny terminal.",
    idea="A client is just another TCP connection that speaks our protocol. Writing one shows you what MockTrade's runner will have to do later.",
    build=[
        "File `src/mockcme/app/client.py`, run with `python -m mockcme.app.client --session T1 --port 9001`.",
        "Commands: `buy GCZ6 3 2400.5`, `sell GCZ6 3 2400.5 fak`, `cancel 7`, `modify 7 2400.6 2`, `quit`.",
        "Print every report in one readable line, e.g. `TRADE  #7 BUY GCZ6 3@2400.5  filled 3  left 0`.",
    ],
    tests=["Unit test only the parser: `parse(\"buy GCZ6 3 2400.5\")` returns the right NewOrder message. Try the rest by hand with two terminals."],
    depends="#17")))

I.append((E2, ["code", "release"], "Start the exchange with one command; release v0.2.0", body(
    "`python -m mockcme` starts the whole exchange, and an end-to-end test proves it works.",
    build=[
        "File `src/mockcme/__main__.py` plus `app/main.py`: build the engine with `INSTRUMENTS`, set every instrument OPEN, start the gateway on port 9001, and print `MockCME listening on 9001`.",
        "File `tests/test_end_to_end.py`: start the app on port 0, connect two clients, trade, check both receive the right reports.",
        "CHANGELOG `## v0.2.0`, bump the version, merge, then publish the release with tag `v0.2.0`.",
    ],
    done=["You traded with yourself from two terminals", "Release v0.2.0 exists"],
    depends="#18")))

# ---------------- E3 ----------------
E3 = "E3 · Market data"
I.append((E3, ["code", "marketdata"], "Book changes as level updates (New, Change, Delete)", body(
    "Turn every change in a book into the small edits a listener needs to keep its own copy.",
    idea="This is MDP's Market by Price. Compare the top 10 levels before and after an event and describe the difference: **New** at level N (inserts and pushes the others down), **Change** at level N (same price, new quantity), **Delete** at level N (removes and pulls the others up).",
    build=[
        "File `src/mockcme/marketdata/levels.py`. `@dataclass LevelUpdate` with `action`, `side`, `level` (1–10), `price`, `qty` and `count`.",
        "`diff(before, after) -> list[LevelUpdate]` for one side, where before/after are `book.levels(side)` lists.",
        "`apply(levels, update) -> levels`: what a listener does. A test can then check `apply`-ing all updates to `before` gives `after`.",
    ],
    tests=[
        "The worked example from the CME guide: bids 2400.4/2400.2/2400.1, then a 2400.3 × 7 arrives → one New at level 2",
        "Quantity change at the best price → one Change at level 1",
        "The best level fully traded → Delete at level 1",
        "Random test: for 1,000 random before/after pairs, applying `diff` to `before` always gives `after`",
    ],
    spec="Write the update actions in `docs/spec/market-data.md`",
    depends="#19")))

I.append((E3, ["code", "marketdata"], "Numbered packets over UDP", body(
    "Broadcast updates in packets that listeners can check for gaps.",
    idea="Every packet gets `seq` (one counter for the whole feed). Every update for one instrument gets `rpt_seq` (a counter per instrument). UDP may lose packets, so the numbers are how a listener finds out.",
    build=[
        "File `marketdata/packets.py`: `Packet(seq, sending_time, messages)`, encoded as one JSON datagram.",
        "File `marketdata/publisher.py`: `Publisher` holds the counters and sends packets with `asyncio`'s datagram endpoint to a host and port (localhost in tests).",
        "Hook it to the engine task: after each command, diff the touched book and publish the updates in one packet, with the end-of-event flag set on the last message.",
    ],
    tests=[
        "Three events → packets with seq 1, 2, 3",
        "rpt_seq counts separately for GCZ6 and SIZ6",
        "A test listener on localhost receives and decodes the packets",
    ],
    depends="#20")))

I.append((E3, ["code", "marketdata"], "Trades, instrument definitions and status on the feed", body(
    "Publish everything a screen needs besides the book: trades (for Time & Sales), what instruments exist and their trading state.",
    build=[
        "`TradeSummary`: price, qty and aggressor side, sent before the book updates the trade caused.",
        "`InstrumentDefinition`: symbol, product, tick size and contract size. Sent at startup and repeated every N seconds.",
        "`SecurityStatus`: sent on every state change.",
    ],
    tests=["A trade produces a TradeSummary then level updates, in one packet", "SetState HALT produces a SecurityStatus"],
    spec="Add the three messages to `docs/spec/market-data.md`",
    depends="#21")))

I.append((E3, ["code", "marketdata"], "Snapshots for recovery", body(
    "A listener who joined late or lost packets can get a full copy of the book.",
    idea="A snapshot is a photo of the whole book plus the counters it's correct up to (`last_seq`, `rpt_seq`). The listener loads the photo, then applies only newer updates.",
    build=[
        "File `marketdata/snapshot.py`: `snapshot(book, rpt_seq, last_seq) -> dict`.",
        "Serve snapshots in a loop on a second UDP port (like CME's Market Recovery feed), one instrument per packet, cycling through all of them.",
    ],
    tests=["Snapshot of a known book has the right levels and counters", "Load the snapshot, then apply the next updates → equals the live book"],
    depends="#22")))

I.append((E3, ["code", "marketdata", "release"], "Feed listener tool, gap test and release v0.3.0", body(
    "A small program that rebuilds books from the feed. It proves the feed is right, and MockTrade's newsroom will copy it.",
    build=[
        "File `src/mockcme/app/listener.py`: join the feed, take snapshots to start, apply updates, print the top 5 levels of one symbol live.",
        "Gap handling: when `seq` jumps, mark the book stale, wait for its snapshot, recover.",
        "Add a test switch to the publisher that drops one packet on purpose.",
        "CHANGELOG `## v0.3.0`, bump the version, then publish the `v0.3.0` release.",
    ],
    tests=["With the drop switch on, the listener detects the gap, recovers and ends equal to the exchange's book"],
    done=["Listener shows a live book while you trade from the client", "Release v0.3.0 exists"],
    depends="#23")))

# ---------------- E4 ----------------
E4 = "E4 · Real data replay"
I.append((E4, ["code", "replay"], "Load a real COMEX day (Databento) into replay events", body(
    "Read the day of data you downloaded into simple events the exchange can play.",
    build=[
        "Use the Databento file from MockTrade issue #18. `pip install databento`, add it to `pyproject.toml` dependencies.",
        "File `replay/loader.py`: read the file, convert each record to `ReplayEvent(ts, symbol, kind, ...)` with prices in ticks.",
        "Keep the data out of git: `data/` is already in `.gitignore`.",
    ],
    tests=["A tiny sample file (a few hundred records, small enough to commit under `tests/data/`) loads into the expected events"],
    depends="#24, MockTrade #18", note="Steps get more detail when you reach this issue.")))
I.append((E4, ["code", "replay"], "Replay player with a speed knob", body(
    "Play the events in time order at 1×, 10× or full speed, using the exchange's clock.",
    build=["File `replay/player.py`: the clock follows the data's timestamps × speed.", "Commands: start, pause, set speed."],
    tests=["At full speed, 1,000 events play in order and the clock ends at the last timestamp"],
    depends="#25", note="Steps get more detail when you reach this issue.")))
I.append((E4, ["code", "replay"], "Publish the replayed market on the feed", body(
    "The real market's book and trades appear on MockCME's feed as if they happened live.",
    build=["Replay events update a *market book* per symbol", "Those changes go through the same level diff and publisher as normal orders"],
    tests=["The listener rebuilds the replayed book exactly"],
    depends="#26", note="Steps get more detail when you reach this issue.")))
I.append((E4, ["code", "replay"], "Fill model: your orders against the replayed market", body(
    "When you trade during a replay, your orders fill the way they plausibly would have in the real market.",
    idea="Aggressive orders fill against the replayed best prices, up to the shown quantity. Passive orders fill when real trades happen at your price, but only after the quantity that was ahead of you in the queue has traded.",
    build=["Write the model's rules and its limits in `docs/spec/rules.md` first", "Then build it and test it with hand-made replay events"],
    depends="#27", note="Steps get more detail when you reach this issue.")))
I.append((E4, ["release"], "Release v0.4.0", body(
    "Ship replay.",
    build=["CHANGELOG `## v0.4.0`, bump the version, then publish the `v0.4.0` release"],
    done=["Release v0.4.0 exists"], depends="#28")))

# ---------------- E5–E8 (brief) ----------------
E5 = "E5 · Safety & recovery"
for title, goal in [
    ("Cancel on disconnect", "When a session drops or goes silent, cancel all its working orders (it can opt out at logon)."),
    ("Message throttle per session", "Reject messages above N per second for a session, so one runaway client can't flood the exchange."),
    ("Price banding", "Reject orders priced too far from the last trade or the best price (fat-finger protection)."),
    ("Self-match prevention", "Stop a firm's buy trading with its own sell. Cancel the resting or the new order, as the order says."),
    ("Mass cancel", "One message cancels all of a session's orders, for one symbol or all."),
    ("Journal and restart: GTC orders survive", "Write every inbound command to an append-only file. On restart, replay it to rebuild the exact same books."),
    ("Resend on reconnect", "A client that reconnects can ask for the messages it missed by sequence number."),
]:
    I.append((E5, ["code", "detail-later"], title, body(goal, note="Steps get written in detail when you reach this milestone.")))
I.append(("E6 · Drop copy", ["code", "detail-later"], "Drop copy feed", body(
    "Send a copy of every execution report on a separate connection for a firm's back office (MockTrade's accounts office reads it).",
    note="Steps get written in detail when you reach this milestone.")))
for title, goal in [
    ("Listed calendar spreads with legs", "Spread instruments with their own book, whose trades create leg fills with the right ratios."),
    ("Implied prices", "Build spread prices from outright books (implied in) and outright prices from spreads (implied out), and match against them."),
]:
    I.append(("E7 · Spreads", ["code", "core", "detail-later"], title, body(goal, note="Steps get written in detail when you reach this milestone.")))
I.append(("E8 · Second venue", ["code", "detail-later"], "Run a second venue", body(
    "Start two MockCME instances with different instruments and ports, as two exchange sites for MockTrade's routing work.",
    note="Steps get written in detail when you reach this milestone.")))

if __name__ == "__main__":
    print(len(I), "issues")
    for n, (m, l, t, b) in enumerate(I, 1):
        print(n, m, "|", t)
