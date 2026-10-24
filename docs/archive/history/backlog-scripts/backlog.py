"""MockTrade backlog: milestones, labels, issues. Rendered into GitHub by create_backlog.py."""

LATER = ("\n\n> 🧭 **Detailed steps come later.** The exact step-by-step instructions for this issue are added "
         "when we reach it, because they depend on decisions made in earlier issues. The goal and the "
         "\"done when\" checks below are fixed now.")

def body(goal, why, learn, steps, done, deps=None, later=False, extra=None):
    s = f"## Goal\n{goal}\n\n## Why it matters\n{why}\n"
    if learn:
        s += "\n## Learn first\n" + "\n".join(f"- {x}" for x in learn) + "\n"
    s += "\n## Steps\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1)) + "\n"
    if extra:
        s += "\n" + extra + "\n"
    s += "\n## Done when\n" + "\n".join(f"- [ ] {x}" for x in done) + "\n"
    if deps:
        s += "\n## Depends on\n" + ", ".join(deps) + "\n"
    s += ("\n---\n**How to work on this issue:** read it top to bottom → do the steps on a branch → open a pull request "
          "with `Closes #<this number>` in its description → wait for the green tick and the review → merge. "
          "Stuck? Comment here or tell Claude \"stuck on #<number>\".")
    if later:
        s += LATER
    return s

MILESTONES = [
 ("Phase 0 · Setup", "Repo, board, laptop, first green test. One short session."),
 ("Phase 1 · The trader", "Discovery: interview the trader (you), write and rank user stories."),
 ("Phase 2 · Hard numbers", "Speed, load, reliability, recovery and audit rules with real numbers."),
 ("Phase 3 · Study CME", "Read the real exchange's specs and rules; get one day of real COMEX data."),
 ("Phase 4 · Business model", "Define every noun (order, fill, position…), how they relate, and the ID scheme."),
 ("Phase 5 · Architecture", "MockTrade's components and the big technology decisions, each recorded as an ADR."),
 ("Phase 6 · Contracts", "Every message, the order life story, the step-by-step journeys, the UI API."),
 ("Phase 7 · Failures & budgets", "What can break and what we do; time and capacity budgets."),
 ("Phase 8 · Test strategy", "MockCME design and the test scenarios for v0.1."),
 ("v0.1 · Walking skeleton", "Click buy → guard → runner → MockCME → fill → shown on screen."),
 ("v0.2 · Safe orders", "Real risk checks; full order life story (modify, cancel, reject)."),
 ("v0.3 · Real market", "Real COMEX history replayed through the newsroom; live ladder."),
 ("v0.4 · Books", "Positions, P&L, reconciliation, restart without losing orders."),
 ("v0.5 · Robots", "Event-driven algo engine and an ASE-style spread hedger."),
 ("v1.0 · Production-grade", "Monitoring, logs, failure handling, London/Aurora groups, docs complete."),
]

LABELS = [
 ("type:setup", "c5def5", "Tools, repo, laptop"), ("type:learn", "fbca04", "Understand a concept first"),
 ("type:design", "5319e7", "Writes a design document or ADR"), ("type:build", "0e8a16", "Writes code"),
 ("type:test", "1d76db", "Writes or runs tests"), ("type:release", "b60205", "Tags a release"),
 ("type:bug", "d73a4a", "Something is broken"), ("detail-later", "ededed", "Step-by-step added when we reach it"),
 ("comp:mockcme", "bfdadc", "Fake exchange"), ("comp:mds", "bfdadc", "Newsroom / market data"),
 ("comp:appserver", "bfdadc", "Reception desk"), ("comp:rom", "bfdadc", "Security guard / risk"),
 ("comp:sor", "bfdadc", "Runner / order routing"), ("comp:accounts", "bfdadc", "Positions, P&L, reconcile"),
 ("comp:algos", "bfdadc", "Robots and spread hedger"), ("comp:ui", "bfdadc", "Trader's screen"),
 ("comp:common", "bfdadc", "Shared messages, IDs, clock, logging"), ("comp:ci", "bfdadc", "CI and repo tooling"),
 ("comp:deploy", "bfdadc", "Docker, running the groups"),
]

I = []  # (title, milestone_index, labels, body)
def issue(title, m, labels, b): I.append((title, m, labels, b))

# ============================ PHASE 0 ============================
issue("Clean up merged branches and adopt the pull-request-only rule", 0, ["type:setup", "comp:ci"], body(
 "Remove branches that are already merged, and from now on change `main` only through pull requests.",
 "On 12 Oct, `ci.yml` was committed straight to `main` before the test arrived, so CI went red. Pull requests stop that: the robot tests a change before it joins `main`.",
 ["A **branch** is your own copy of the code to work on. `main` is the official copy.",
  "A **merged** branch has already given its changes to `main`; keeping it only adds clutter."],
 ["On GitHub open **Code → Branches** (link under the branch dropdown).",
  "Delete `ayushsharma05-patch-1` and `copilot/update-readme-and-docs` if they are still listed (bin icon). Both are merged.",
  "Go to **Settings → General → Pull Requests** and tick **Automatically delete head branches**. GitHub will now delete a branch for you when its pull request merges.",
  "Promise to yourself: from now on, no commits directly to `main`. Every change = branch → pull request → green tick → merge."],
 ["Only `main` is listed under Branches", "\"Automatically delete head branches\" is ticked"]))

issue("Create the GitHub Projects board", 0, ["type:setup", "comp:ci"], body(
 "Create the board that shows every issue and where it is: Backlog → To do → In progress → Review → Done.",
 "The board is how you (and anyone reading your portfolio) see the whole project at a glance. Real teams run their week from it.",
 ["An **issue** is one piece of work. A **milestone** groups issues into a phase or release. The **board** shows their status."],
 ["In the MockTrade repo click **Projects → Link a project → New project**.",
  "Choose the **Board** template and name it `MockTrade`.",
  "Rename the columns to: `Backlog`, `To do`, `In progress`, `Review`, `Done`.",
  "Open the board's **⋯ → Workflows**. Turn on **Auto-add to project** for repository `MockTrade` with filter `is:issue is:open`. New issues now appear on the board automatically.",
  "Click **+ Add item** at the bottom of Backlog, type `#`, and add every existing open issue (or use the auto-add workflow above).",
  "Move this issue to **In progress** while you do it, then to **Done**."],
 ["The board exists with the five columns", "All open issues appear on it", "Auto-add is on"]))

issue("Set up your Windows laptop: Git, Python and VS Code", 0, ["type:setup"], body(
 "Install the three tools every Python developer uses, and check each one works.",
 "So far you have only worked on GitHub's website. Real development happens on your own computer: you write and run code locally, then push it.",
 ["**Git** keeps the history of your code on your laptop and syncs it with GitHub.",
  "**Python** runs our code. **pip** is Python's app store for libraries.",
  "**VS Code** is the editor where you write code.",
  "**Terminal / PowerShell** is the text window where you type commands."],
 ["Install **Git for Windows** from https://git-scm.com/download/win — accept the defaults.",
  "Install **Python 3.12** from https://www.python.org/downloads/ — on the first screen **tick \"Add python.exe to PATH\"** (forgetting this is the #1 beginner problem).",
  "Install **VS Code** from https://code.visualstudio.com/. Open it, go to Extensions (Ctrl+Shift+X) and install **Python** (by Microsoft).",
  "Open **PowerShell** and check each tool, one line at a time:\n   ```\n   git --version\n   python --version\n   code --version\n   ```\n   Each should print a version number. `python --version` should say 3.12.x.",
  "Tell Git who you are (it stamps this on every commit):\n   ```\n   git config --global user.name \"Ayush Sharma\"\n   git config --global user.email \"<the email on your GitHub account>\"\n   ```"],
 ["All three `--version` commands print a version", "`git config --global user.name` prints your name"]))

issue("Clone MockTrade and run the tests on your laptop", 0, ["type:setup", "type:test"], body(
 "Copy the repo onto your laptop, create a private Python environment for it, and see `1 passed` locally.",
 "This is the loop you will repeat thousands of times: change code → run tests locally → push. Running tests locally first is faster than waiting for CI.",
 ["**Clone** = download the repo with its full history.",
  "A **virtual environment** (`.venv`) is a private box of Python libraries for one project, so projects don't fight over versions. It is already in `.gitignore`, so it never gets uploaded."],
 ["In PowerShell go to where you keep projects, e.g. `cd ~\\Documents`.",
  "Clone:\n   ```\n   git clone https://github.com/ayushsharma05/MockTrade.git\n   cd MockTrade\n   ```\n   The first time, a browser window asks you to sign in to GitHub — that is Git Credential Manager; approve it.",
  "Create and switch on the virtual environment:\n   ```\n   python -m venv .venv\n   .venv\\Scripts\\Activate.ps1\n   ```\n   Your prompt now starts with `(.venv)`. If PowerShell says scripts are disabled, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and try again.",
  "Install the test tool and run the tests:\n   ```\n   pip install pytest\n   pytest\n   ```\n   You should see `1 passed`.",
  "Open the project in VS Code: `code .` — then press Ctrl+Shift+P → \"Python: Select Interpreter\" → choose the one inside `.venv`."],
 ["`pytest` prints `1 passed` on your laptop", "VS Code opens MockTrade using the `.venv` interpreter"], deps=["#5"]))

issue("Your first pull request from the laptop: fix test style and add requirements-dev.txt", 0, ["type:setup", "type:test", "comp:ci"], body(
 "Make your first change entirely from your laptop: branch → edit → test → commit → push → pull request → merge.",
 "It practises the full professional loop once, on something tiny, before real code. It also fixes two habits: Python's 4-space indentation, and listing tools in a file instead of installing them by hand.",
 ["**PEP 8** is Python's official style guide: 4 spaces per indentation level.",
  "`requirements-dev.txt` lists the tools developers need. CI and your laptop then install exactly the same things.",
  "A **commit** saves a snapshot with a message. **Push** sends your commits to GitHub."],
 ["Make a branch: `git switch -c chore/test-style`",
  "In `tests/test_smoke.py` change the indentation of the `assert` line from 2 spaces to 4.",
  "Create `requirements-dev.txt` at the top level containing one line: `pytest`",
  "In `.github/workflows/ci.yml` replace `- run: pip install pytest` with `- run: pip install -r requirements-dev.txt`",
  "Run `pytest` locally — still `1 passed`.",
  "Save the snapshot and send it:\n   ```\n   git add .\n   git commit -m \"Fix test indentation and add requirements-dev.txt\"\n   git push -u origin chore/test-style\n   ```",
  "On GitHub, click **Compare & pull request**. In the description write `Closes #<this issue number>`. Create it.",
  "Wait for the green tick, ask Claude to review, then merge."],
 ["PR merged with a green tick", "This issue closed automatically by the PR", "Branch auto-deleted"], deps=["#6", "#3"]))

# ============================ PHASE 1 ============================
issue("Learn: user stories and how product teams interview users", 1, ["type:learn"], body(
 "Understand what a user story is and why teams write them before any design.",
 "Experts design from what users need, not from boxes and arrows. User stories turn needs into small, testable statements.",
 ["**User story**: \"As a <who>, I want <what>, so that <why>.\" Example: \"As a spread trader, I want leg 2 hedged automatically when leg 1 fills, so that I am never left holding one leg.\"",
  "**Acceptance criteria**: how we will know it works, written as **Given / When / Then**: \"Given a working Dec–Feb spread order, when the Feb leg fills, then a Dec order is sent within 1 ms.\"",
  "**Discovery interview**: the team asks the user to *describe what they do*, not what they want built."],
 ["Ask Claude to explain user stories with 3 examples from your own desk.",
  "Write 2 practice stories in a comment on this issue; Claude gives feedback."],
 ["You can explain the three parts of a story and Given/When/Then in your own words", "2 practice stories commented and reviewed"]))

issue("Trader interview: answer the discovery questions", 1, ["type:design"], body(
 "Answer the questions below as the trader, in comments on this issue (one comment per question is fine).",
 "Your answers are the raw material for every user story. You are the user — this is your biggest advantage.",
 None,
 ["Answer each question in plain words. Short is fine; Claude will ask follow-ups.",
  "Describe how *you* work in general terms only. Do not paste company screenshots, account numbers, limits or anything confidential."],
 ["All 10 questions answered", "Claude's follow-up questions answered"],
 extra="## Questions\n1. Walk through a normal day: from opening your trading screen to logging off.\n2. Which windows/widgets do you keep open, and where on the screen?\n3. How do you decide which spread to trade and at what price?\n4. How do you place a spread order today — legs by hand, an autospreader, an algo?\n5. While an order is working, what do you watch?\n6. When one leg fills, what must happen next, and how fast?\n7. What safety limits do you live with, and what happens when you hit one?\n8. What mistakes have you seen (fat finger, legging, wrong month) and how should a system stop them?\n9. What annoys you most in the tools you use today? What do you wish existed?\n10. At the end of the day, what do you check (positions, P&L, fills)?"))

issue("Write the user stories (docs/requirements/user-stories.md)", 1, ["type:design"], body(
 "Turn the interview into 20–40 user stories with acceptance criteria.",
 "Every later document and every line of code must trace back to a story. A box with no story behind it should not exist.",
 None,
 ["Claude drafts a story list from your interview answers in a comment here.",
  "You edit it: remove what's wrong, add what's missing, reword in your language.",
  "Create `docs/requirements/user-stories.md` with one section per area: Market data · Order entry · Spreads · Risk · Positions & P&L · Algos · Operations.",
  "Each story gets an ID (`US-01`…), the story sentence, and 1–3 Given/When/Then lines.",
  "Branch → PR with `Closes #<this issue>` → review → merge."],
 ["20+ stories, each with an ID and acceptance criteria", "Merged through a reviewed PR"], deps=["#9"]))

issue("Rank the stories and define what each release contains", 1, ["type:design"], body(
 "Mark every story **Must / Should / Later** and assign each Must/Should to a release (v0.1 … v1.0).",
 "Ranking is what stops a project from never finishing. v0.1 must be tiny.",
 ["**MoSCoW** ranking: Must have, Should have, Could have, Won't have (yet)."],
 ["Add a `Priority` and `Release` column to every story in `user-stories.md`.",
  "Check v0.1 has only what the walking skeleton needs (one order type, one product, one user).",
  "Claude reviews for gaps: does every release have its stories?",
  "PR → review → merge."],
 ["Every story has a priority and a release", "v0.1 list fits in 5–8 stories"], deps=["#10"]))

# ============================ PHASE 2 ============================
issue("Learn: latency, throughput, availability and recovery", 2, ["type:learn"], body(
 "Understand the four kinds of \"hard numbers\" every trading system is judged by.",
 "Features say *what* the system does; hard numbers say *how well*. They decide many design choices later (e.g. which messaging to use).",
 ["**Latency**: how long one thing takes (click → exchange acknowledgement). Measured in ms (thousandths) or µs (millionths).",
  "**Throughput**: how many things per second (orders/s, price updates/s).",
  "**Availability**: how much of the time it works. **Recovery**: how fast it comes back after a crash, and what may be lost (ideally: nothing).",
  "**Percentiles**: p50 = typical, p99 = the slow 1%. Traders care about p99."],
 ["Ask Claude for a flowchart of where time goes in one order's journey.",
  "Comment here: one example each of latency, throughput, availability and recovery from your desk."],
 ["You can explain all four and what p99 means"]))

issue("Write the hard numbers (docs/requirements/hard-numbers.md)", 2, ["type:design"], body(
 "Set target numbers for MockTrade, each with a one-line reason.",
 "Numbers turn \"fast\" into something we can test. Phase 7 splits them into per-component budgets.",
 None,
 ["Create `docs/requirements/hard-numbers.md` with a table: Area · Target · Reason · How we measure.",
  "Suggested starting points (you decide, laptop-scale): click → MockCME ack p99 < 20 ms; MockCME matching < 1 ms; price change → ladder < 100 ms; 200 orders/s; 20,000 price updates/s during replay.",
  "Reliability rules: no accepted order is ever lost; a restarted guard (ROM) recovers every working order; every order event is written to an audit log.",
  "Safety rules: no order reaches MockCME without passing risk checks.",
  "PR → review → merge."],
 ["Every number has a target, reason and measurement method", "Merged through a reviewed PR"], deps=["#12", "#11"]))

# ============================ PHASE 3 ============================
issue("Learn: how an exchange works inside", 3, ["type:learn"], body(
 "Understand the matching engine: how an exchange's order book matches buyers and sellers.",
 "MockCME *is* a small exchange. You can't design it, or the runner that talks to it, without this.",
 ["**Order book**: for each price, a queue of resting orders.",
  "**Price-time priority**: best price first; at the same price, first come first served.",
  "**Aggressive vs passive**: an order that crosses the spread trades now (aggressive); one that doesn't rests in the book (passive).",
  "**Execution report**: the exchange's message saying accepted / filled / cancelled / rejected."],
 ["Ask Claude for a step-by-step flowchart of 5 orders arriving and matching.",
  "On paper, simulate: book has sells at 2400.4 (3 lots) and 2400.5 (5 lots); a buy for 6 lots at 2400.5 arrives. Write the fills in a comment."],
 ["Paper simulation correct", "You can explain price-time priority"]))

issue("Read CME MDP 3.0 (market data) and write notes", 3, ["type:design"], body(
 "Read the overview of CME's market data protocol and write what MockCME should copy.",
 "Copying a real exchange's design teaches you how experts solved the same problems (gaps, snapshots, book updates).",
 ["Spec: https://cmegroupclientsite.atlassian.net/wiki/x/YzJCGw (Message Specification) and the Message Schema page."],
 ["Read only the overview pages: message types, incremental refresh vs snapshot, sequence numbers and gap recovery, book depth.",
  "Create `docs/exchange/cme-mdp3-notes.md`: for each concept, 2–3 lines in your words + \"MockCME will copy this? yes/no/simplified\".",
  "PR → review → merge."],
 ["Notes cover: message types, incremental vs snapshot, sequence numbers & gaps, book depth", "Merged through a reviewed PR"], deps=["#14"]))

issue("Read CME iLink 3 (order entry) and write notes", 3, ["type:design"], body(
 "Read the overview of CME's order-entry protocol and write what our runner and MockCME should copy.",
 "Order entry rules (sessions, sequence numbers, cancel-on-disconnect) are where real trading systems get their reliability.",
 ["Find it on the CME Client Systems Wiki by searching **\"iLink 3 Binary Order Entry\"**."],
 ["Read the overview: session logon/heartbeat, sequence numbers, NewOrderSingle, Cancel, Cancel/Replace, Execution Report, cancel-on-disconnect, message throttles.",
  "Create `docs/exchange/cme-ilink3-notes.md` with the same format as the MDP 3.0 notes.",
  "PR → review → merge."],
 ["Notes cover the 8 topics above", "Merged through a reviewed PR"], deps=["#14"]))

issue("Write product facts for gold, silver and copper futures", 3, ["type:design"], body(
 "Record the contract rules MockCME must enforce for GC, SI and HG.",
 "Tick size, multiplier and trading hours are hard rules; getting one wrong makes every price and P&L wrong.",
 None,
 ["From cmegroup.com contract specs pages, record for each: tick size, tick value ($), contract size, listed months, trading hours, daily maintenance break, last trading day rule.",
  "Create `docs/exchange/products.md` as a table, with the source link per product.",
  "PR → review → merge."],
 ["Table complete for GC, SI, HG with sources"], deps=["#14"]))

issue("Get one day of real COMEX data (Databento) and describe it", 3, ["type:setup", "comp:mockcme"], body(
 "Download one day of real gold futures order book data and document its format.",
 "In v0.3, MockCME replays this so our market behaves exactly like the real one did.",
 ["**MBP-10** = market by price, top 10 levels. **MBO** = market by order (every single order). MBP-10 is enough to start and smaller."],
 ["Sign up at https://databento.com (new users get free credit; check the current amount when you sign up).",
  "In the portal, choose dataset **CME Globex MDP 3.0**, symbol the active gold contract (e.g. `GCZ6` for Dec 2026), schema **MBP-10**, one recent trading day. **Check the cost estimate before downloading** — stay inside the free credit.",
  "Add a line `data/` to `.gitignore` (data never goes into Git), save the file under `data/` on your laptop.",
  "Create `docs/exchange/data.md`: what you downloaded, file size, the columns and what each means.",
  "PR (for `.gitignore` + `data.md`) → review → merge."],
 ["Data file on your laptop, not in Git", "`data.md` describes every column"], deps=["#15", "#17"]))

issue("ADR-0001: which products MockTrade supports first", 3, ["type:design"], body(
 "Write our first Architecture Decision Record: the products and contract months for v0.1 → v1.0.",
 "An ADR records a decision, the options, and why — so future-you knows why things are the way they are.",
 ["**ADR format**: Title · Status · Context · Options · Decision · Consequences. One page."],
 ["Create `docs/adr/0001-products.md` using the format above.",
  "Options to weigh: GC only vs GC+SI+HG; outrights only vs calendar spreads too.",
  "PR → review → merge."],
 ["ADR merged with a clear decision"], deps=["#17", "#11"]))

# ============================ PHASE 4 ============================
issue("Write the glossary (docs/design/glossary.md)", 4, ["type:design"], body(
 "One-line definitions for every word used in MockTrade: order, fill, leg, tick, position, session…",
 "Teams lose weeks when two people mean different things by one word.",
 None,
 ["Collect every noun from the user stories and the CME notes.",
  "Write each as `**Word** — one plain sentence`.",
  "PR → review → merge."],
 ["Every noun in the stories is defined"], deps=["#10", "#16"], later=True))

issue("Domain model: entities, fields and relationships", 4, ["type:design"], body(
 "Define MockTrade's core data: Instrument, Product, Spread, Account, Trader, Order, Fill, Position, RiskLimit.",
 "These become the data classes in `common/` — the shared language of every service.",
 ["**Entity** = a thing with an identity (an order). **Relationship** = how things connect (an order has many fills)."],
 ["For each entity list its fields with type and meaning.",
  "Draw the relationships as a Mermaid diagram (GitHub renders it).",
  "Create `docs/design/domain-model.md`.",
  "PR → review → merge."],
 ["All 9 entities defined with fields", "Diagram renders on GitHub"], deps=["#20"], later=True))

issue("ADR-0002: the ID scheme", 4, ["type:design"], body(
 "Decide how orders, fills and sessions are identified end to end (client ID, internal ID, exchange ID).",
 "Without clear IDs you cannot match a fill to its order, or reconcile with the exchange.",
 None,
 ["Write `docs/adr/0002-ids.md`: who creates each ID, format, uniqueness, how they map to each other.",
  "PR → review → merge."],
 ["ADR merged"], deps=["#21"], later=True))

# ============================ PHASE 5 ============================
issue("Architecture document for MockTrade", 5, ["type:design"], body(
 "Adapt the reference architecture into MockTrade's own: components, where each runs (London/Aurora groups), who talks to whom.",
 "This is the map every later issue builds against. Every box must trace to a story or a hard number.",
 None,
 ["Draw the component diagram (Mermaid) and a table: component · job · talks to · story IDs.",
  "Create `docs/design/architecture.md`.",
  "PR → review → merge."],
 ["Every component traces to a story or hard number"], deps=["#22", "#13"], later=True))

issue("ADR-0003: programming language and runtime", 5, ["type:design"], body(
 "Decide the language for services (likely Python with asyncio first) and when, if ever, to move hot paths to a faster language.",
 "The choice affects every line of code; writing down why prevents second-guessing later.",
 None,
 ["Compare options against the hard numbers.", "Write `docs/adr/0003-language.md`.", "PR → review → merge."],
 ["ADR merged"], deps=["#23"], later=True))

issue("ADR-0004: how services talk to each other", 5, ["type:design"], body(
 "Choose the messaging between components (fast lane and careful lane), e.g. in-process queues → ZeroMQ/NATS, plus a log for the careful lane.",
 "The reference platform uses Aeron (fast) and Kafka (careful); we pick laptop-friendly equivalents with the same ideas.",
 None,
 ["Compare 2–3 options on latency, simplicity, and learning value.", "Write `docs/adr/0004-messaging.md`.", "PR → review → merge."],
 ["ADR merged"], deps=["#23"], later=True))

issue("ADR-0005: storage", 5, ["type:design"], body(
 "Decide where orders, fills, positions and the audit log are stored (e.g. SQLite first, PostgreSQL later).",
 "Recovery and audit rules from the hard numbers depend on this.",
 None,
 ["Write `docs/adr/0005-storage.md`.", "PR → review → merge."],
 ["ADR merged"], deps=["#23"], later=True))

issue("ADR-0006: UI technology", 5, ["type:design", "comp:ui"], body(
 "Decide how the trader's screen is built (e.g. plain HTML/JS first, React later) and how it gets live updates.",
 "The ladder needs fast, smooth updates; the choice decides how hard that is.",
 None,
 ["Write `docs/adr/0006-ui.md`.", "PR → review → merge."],
 ["ADR merged"], deps=["#23"], later=True))

# ============================ PHASE 6 ============================
issue("Message dictionary v1", 6, ["type:design", "comp:common"], body(
 "Define every message: NewOrder, CancelOrder, ReplaceOrder, ExecutionReport, MarketDataUpdate, Subscribe… with every field and type.",
 "This is the heart of the design: the language all components speak. It was the biggest unknown in the reference platform.",
 None,
 ["Create `docs/design/messages.md`: one table per message.", "Mark which component sends and receives each.", "PR → review → merge."],
 ["Every arrow in the architecture has a message"], deps=["#25", "#22"], later=True))

issue("Order life story (state machine)", 6, ["type:design", "comp:rom"], body(
 "Define every order state, what moves an order between them, and what is never allowed.",
 "The guard, the runner and the screen must agree exactly on what state an order is in.",
 None,
 ["Draw the state diagram (Mermaid).", "List allowed and forbidden transitions.", "Create `docs/design/order-lifecycle.md`; PR → merge."],
 ["Diagram + transition table merged"], deps=["#28"], later=True))

issue("Sequence diagrams for the six key journeys", 6, ["type:design"], body(
 "Step-by-step message sequences for: place & fill, partial fill, cancel, reject, price update, restart.",
 "Sequences catch design gaps before code does.",
 None,
 ["One Mermaid sequence diagram per journey in `docs/design/journeys.md`.", "PR → review → merge."],
 ["Six diagrams merged"], deps=["#29"], later=True))

issue("UI ↔ App Server API", 6, ["type:design", "comp:appserver", "comp:ui"], body(
 "Define the messages between the browser and the App Server (login, subscribe, place order, updates).",
 "The screen and the server are built separately; this contract lets them meet.",
 None,
 ["Write `docs/design/ui-api.md`.", "PR → review → merge."],
 ["API doc merged"], deps=["#28", "#27"], later=True))

# ============================ PHASE 7 ============================
issue("Failure catalogue", 7, ["type:design"], body(
 "List at least 15 things that can break and what MockTrade does for each.",
 "This is what separates expert designs from amateur ones.",
 ["Examples: MockCME disconnects; duplicate fill; guard crashes mid-order; slow browser; clock jumps; replay file ends."],
 ["Table: failure · how we detect it · what we do · how we test it.", "Create `docs/design/failures.md`; PR → merge."],
 ["15+ failures, each with a test idea"], deps=["#30"], later=True))

issue("Latency and capacity budget", 7, ["type:design"], body(
 "Split each hard number into a time budget per hop, and set capacity limits per component.",
 "If every hop stays in budget, the whole journey meets its target.",
 None,
 ["Create `docs/design/budgets.md`.", "PR → review → merge."],
 ["Every hop of the order journey has a budget"], deps=["#13", "#30"], later=True))

# ============================ PHASE 8 ============================
issue("MockCME design", 8, ["type:design", "comp:mockcme"], body(
 "Design the fake exchange: order book, matching rules, supported order types, execution reports, market data output, replay mode.",
 "Everything else talks to MockCME; it must behave like a real exchange.",
 None,
 ["Write `docs/design/mockcme.md` based on the CME notes.", "PR → review → merge."],
 ["Design merged"], deps=["#15", "#16", "#28"], later=True))

issue("Test plan and v0.1 scenarios", 8, ["type:test"], body(
 "Write how we test (unit, integration, end-to-end, failure drills) and the exact scenarios v0.1 must pass.",
 "Scenarios written before code are the definition of \"it works\".",
 None,
 ["Create `docs/test/test-plan.md` with the v0.1 scenario list (Given/When/Then).", "PR → review → merge."],
 ["v0.1 scenarios listed"], deps=["#34", "#32"], later=True))

# ============================ v0.1 ============================
for t, labels, goal, deps in [
 ("common: message classes and tests", ["type:build", "comp:common"], "Implement the v1 messages as Python data classes with tests.", ["#28"]),
 ("MockCME: order book and price-time matching", ["type:build", "comp:mockcme"], "Implement the order book and matching for limit orders, with tests for every matching rule.", ["#34", "#36"]),
 ("MockCME: network interface", ["type:build", "comp:mockcme"], "Accept orders over the network and send execution reports back.", ["#37", "#25"]),
 ("SOR (runner): minimal version", ["type:build", "comp:sor"], "Forward orders to MockCME and return its execution reports.", ["#38"]),
 ("ROM (guard): minimal version", ["type:build", "comp:rom"], "Receive orders, keep the firm's order book, pass orders to the runner (no risk checks yet).", ["#39", "#29"]),
 ("App Server (reception desk): minimal version", ["type:build", "comp:appserver"], "One user, a WebSocket endpoint, forwards orders to the guard and updates back to the screen.", ["#40", "#31"]),
 ("UI: order ticket and order list", ["type:build", "comp:ui"], "A screen to enter buy/sell, price, quantity, and see orders and fills update live.", ["#41"]),
]:
    issue(t, 9, labels, body(goal, "Part of the walking skeleton: the thinnest version of the whole road.", None,
      ["Branch → code + tests → PR → review → merge."], ["Tests pass in CI", "You can explain every line"], deps=deps, later=True))
issue("End-to-end test and release v0.1", 9, ["type:release", "type:test"], body(
 "One automated test: click buy → fill appears on screen. Then tag the release `v0.1`.",
 "Proves the whole road exists end to end.", None,
 ["Write the end-to-end test.", "Write release notes.", "Create tag `v0.1` and a GitHub Release."],
 ["End-to-end test green in CI", "Release v0.1 published"], deps=["#42"], later=True))

# ============================ v0.2 ============================
for t, labels, goal, deps in [
 ("ROM: static validation", ["type:build", "comp:rom"], "Reject malformed orders: unknown instrument, wrong tick size, zero quantity.", ["#43"]),
 ("ROM: risk limits", ["type:build", "comp:rom"], "Max order size, max position (including working orders), max working orders, price band.", ["#44"]),
 ("Cancel and modify, end to end", ["type:build", "comp:rom", "comp:sor", "comp:mockcme"], "Cancel and cancel/replace through every component, following the order life story.", ["#45"]),
 ("UI: rejects and order states", ["type:build", "comp:ui"], "Show why an order was rejected and the live state of every order.", ["#46"]),
]:
    issue(t, 10, labels, body(goal, "No unsafe order may ever reach the exchange.", None, ["Branch → code + tests → PR → review → merge."],
      ["Tests pass in CI", "You can explain every line"], deps=deps, later=True))
issue("Release v0.2", 10, ["type:release"], body("Tag `v0.2` with release notes.", "Marks safe orders as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.2 published"], deps=["#47"]))

# ============================ v0.3 ============================
for t, labels, goal, deps in [
 ("MockCME: replay real market data", ["type:build", "comp:mockcme"], "Replay the Databento file so MockCME's book moves exactly like the real day.", ["#18", "#48"]),
 ("MDS (newsroom): book builder, subscriptions, throttler", ["type:build", "comp:mds"], "Build books from MockCME's feed, serve subscriptions, limit update speed for screens.", ["#49"]),
 ("UI: price ladder with click-to-trade", ["type:build", "comp:ui"], "A live depth ladder; clicking a price places an order there.", ["#50"]),
 ("MockCME: fill model for your orders against the replayed market", ["type:build", "comp:mockcme"], "Decide and implement when your orders fill while the market is replayed (queue position).", ["#49"]),
]:
    issue(t, 11, labels, body(goal, "Makes the market realistic.", None, ["Branch → code + tests → PR → review → merge."],
      ["Tests pass in CI", "You can explain every line"], deps=deps, later=True))
issue("Release v0.3", 11, ["type:release"], body("Tag `v0.3` with release notes.", "Marks the real market as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.3 published"], deps=["#51", "#52"]))

# ============================ v0.4 ============================
for t, labels, goal, deps in [
 ("Accounts: positions and P&L from fills", ["type:build", "comp:accounts"], "Turn fills into net positions, average prices, realized and unrealized P&L.", ["#53"]),
 ("Drop copy and reconciliation", ["type:build", "comp:accounts", "comp:mockcme"], "MockCME sends its own copy of fills; a job compares it with our records and alerts on differences.", ["#54"]),
 ("ROM: persistence and restart recovery", ["type:build", "comp:rom"], "Journal every order event; a restarted guard rebuilds every working order.", ["#53", "#26"]),
 ("UI: positions and P&L", ["type:build", "comp:ui"], "Live positions and P&L on screen.", ["#54"]),
]:
    issue(t, 12, labels, body(goal, "Nothing is ever lost or miscounted.", None, ["Branch → code + tests → PR → review → merge."],
      ["Tests pass in CI", "You can explain every line"], deps=deps, later=True))
issue("Release v0.4", 12, ["type:release"], body("Tag `v0.4` with release notes.", "Marks the books as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.4 published"], deps=["#55", "#56", "#57"]))

# ============================ v0.5 ============================
for t, labels, goal, deps in [
 ("Algo engine: event-driven lifecycle", ["type:build", "comp:algos"], "Run algos with OnInit, OnStart, OnPrice, OnOrderEvent, OnTimer, OnStop; parent and child orders.", ["#58"]),
 ("First algo: order slicer", ["type:build", "comp:algos"], "Split a big parent order into small child orders over time.", ["#59"]),
 ("Spread definitions and implied prices", ["type:build", "comp:mds", "comp:algos"], "Define calendar spreads and calculate their bid/ask from the legs.", ["#59", "#19"]),
 ("Spread hedger with legging-risk handling", ["type:build", "comp:algos"], "Work one leg, hedge the other on fill; pay up or unwind if the hedge fails.", ["#61"]),
 ("UI: algo control (MASE-style)", ["type:build", "comp:ui"], "Start, stop and change algos from the screen; see parent and child orders.", ["#62"]),
]:
    issue(t, 13, labels, body(goal, "Your desk's spread logic, running by itself.", None, ["Branch → code + tests → PR → review → merge."],
      ["Tests pass in CI", "You can explain every line"], deps=deps, later=True))
issue("Release v0.5", 13, ["type:release"], body("Tag `v0.5` with release notes.", "Marks the robots as done.", None,
 ["Run all scenarios.", "Tag and publish the release."], ["Release v0.5 published"], deps=["#63"]))

# ============================ v1.0 ============================
for t, labels, goal, deps in [
 ("Structured logging in every service", ["type:build", "comp:common"], "Every service writes structured logs with order IDs so any journey can be traced.", ["#64"]),
 ("Health: running vs ready, plus metrics", ["type:build", "comp:common"], "Each service reports \"up\" and \"ready\" separately, plus key counters, for Prometheus.", ["#65"]),
 ("Grafana dashboard", ["type:build", "comp:deploy"], "One dashboard showing health and key numbers of every service.", ["#66"]),
 ("Docker Compose: London and Aurora groups with fake distance", ["type:build", "comp:deploy"], "Run services as two groups with an artificial delay between them.", ["#66"]),
 ("Failure drills", ["type:test"], "Run the failure catalogue: kill the guard mid-day, disconnect the runner, and prove recovery.", ["#68", "#32"]),
 ("Runbooks", ["type:design"], "For each failure, a short \"what to do at 3 am\" page in `docs/runbooks/`.", ["#69"]),
]:
    issue(t, 14, labels, body(goal, "It runs, heals and explains itself.", None, ["Branch → work + tests → PR → review → merge."],
      ["Done and reviewed", "You can explain every part"], deps=deps, later=True))
issue("Docs complete and release v1.0", 14, ["type:release"], body(
 "README with how to run everything, all design docs up to date, tag `v1.0`.",
 "The finished, portfolio-ready project.", None,
 ["Update README and docs.", "Run every scenario and drill.", "Tag and publish v1.0."],
 ["Release v1.0 published"], deps=["#70"]))
