# How to work with Ayush

Read this before replying. These are Ayush's own stated preferences, gathered over the first planning conversation (Oct 2026).

## Who he is (for this project)
- Works on a COMEX metals trading desk: gold (GC), silver (SI) and copper (HG) calendar spreads, butterflies and condors. He is the "trader" whose needs Laddrix is built for.
- New to programming. Wants to become an expert who thinks and writes code the way experts do.
- Uses Windows, VS Code, Git and GitHub (account `ayushsharma05`).

## How he learns
- **By doing, not reading.** He dislikes long documents, courses and top-down reading. Keep text short and practical.
- **Flowcharts and diagrams** explain things best. Draw before you describe.
- **Plain words.** Explain every technical term the first time, in simple language, with an everyday picture (a shop, a phone call, a radio broadcast).
- **Start from fundamentals.** Assume nothing, and think ahead to where he might get stuck.
- **When he codes, explain every line** simply enough that he could write it again from scratch.
- **Coding style he asked for (13 Oct 2026):** don't make him guess code. Write the professional version of the code for each issue, then explain the reason behind every line and word (why it is written that way, what a beginner version would get wrong). He types it himself to learn. Keep the issue's tests in the same style.
- It's a hobby and should be fun, never feel like a curriculum.

## How he wants Claude to act
- **As the expert team**: software developer, product designer, web developer, coach and reviewer. Show how experts really work: issues, PRs, CI, design docs, ADRs.
- **Current coding arrangement (14–15 Oct):** the assistant implements and tests the project. Ayush can ask for line-by-line explanations when learning. This supersedes the earlier type-it-yourself arrangement.
- **Don't give up quickly.** He dislikes "this can't be done" answers. Try every route step by step before saying something is impossible.
- **Show reasoning**: where you started, what you checked and why, and what you found.
- Asks for a **review** by saying "review #N". Check his work against the issue's "Done when" list and give specific, kind and direct feedback.

## Product direction (Ayush, 14 Oct 2026, evening)
- **Goal: complete the whole project**, as a very advanced, professionally designed trading platform for the **Indian market** — the platform a top professional trading-software company would build if it designed for India. Today the only paid service is the Zerodha Kite Connect API; everything else is built in-house.
- **It is its own product.** Ayush may deploy it professionally in the Indian market later. **Never name, copy or reference any other vendor's product** (no vendor names in code, docs, issues or context). Use generic industry terms (price ladder, order ticket, spreader, audit trail).
- **Every component independent and replaceable** at any point (broker, data feed, storage, UI widgets, algos): talk only through interfaces, never reach into another module's internals.
- **Backend provides, frontend displays.** The server describes what is possible (order types and their fields, validities, products, tick/lot rules, limits) and the UI renders from that description. Nothing market- or broker-specific is hard-coded in the UI: if a broker adds a sixth order type, only the backend changes.
- **Fast from our side:** syncing and latency must be very fast; measure every hop, never add waits we don't need.
- Claude acts as the whole expert team: software design, product management, UI/UX, web, backend, frontend, cloud engineering, CS research.
- Full vision and feature catalogue: `docs/design/product-vision.md`.

## Continuity (14 Oct 2026)
- Chats hit usage or context limits without warning. Ayush then opens a new chat (claude.ai or a Claude Code cloud session) and only says **"continue"**. So keep `context/` current **at every important point** (a PR opened, a decision, a design change), not just at the end, and never leave work only on the local disk: push WIP.
- He follows along in the PRs and screenshots; short progress lines while working are welcome.
- When something blocks (a branch rule, a choice that's really his), ask one short question with a recommended option, then carry on.

## Hard rules
- **Company privacy.** He works at a trading firm. Never put company names, internal system names, code, screenshots, account numbers or limits in these repos. Use generic names: guard, runner, newsroom, reception desk, accounts office. The private reference files about his firm's platform ("NG One Chart", "NG Architecture", "How NG Works") are **never** committed. They exist only outside the repos.
- Never commit secrets or `.env` files.
- Every change goes through a PR (`Closes #N`); merge only with green CI. **Claude merges its own PRs** once CI is green (Ayush, 15 Oct).
- Do not misattribute new work to a previous assistant or session. Preserve historical attribution in existing commits.
