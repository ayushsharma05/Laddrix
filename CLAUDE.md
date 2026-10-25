# Laddrix

(Called MockTrade until 25 Oct 2026; renamed in #457 because other repositories already use that name. The Python package is `laddrix`, settings are `LADDRIX_*`. The GitHub repository keeps its old address until Ayush renames it.)

Ayush's learning project: a personal, professional-style trading platform for **NSE/BSE on Zerodha Kite Connect** (REST for orders, WebSocket for live ticks), with a paper-trading broker that fills against live Kite prices. The earlier COMEX/MockCME track was paused on 14 Oct 2026 and is archived in `docs/archive/`. Design: `docs/design/kite-architecture.md`; all docs: `docs/README.md`; running it: `docs/running-locally.md`.

**Before anything else, read `context/README.md`** (it has a "How to continue" checklist). Code and context now live together on `main`; start new work there. It has how Ayush wants to work, the full history, the current status and every diagram.

The short version:
- **Product direction (14 Oct):** a professional trading platform for the Indian market, its own product (may be deployed commercially later): never name or copy another vendor. Every component replaceable behind an interface. **Backend provides, frontend displays**: the server publishes what's possible (order spec etc.), the UI hard-codes no market rules. See `docs/design/product-vision.md`.
- Ayush is new to programming and learns by doing. Use plain words, flowcharts and short answers, and explain every line of code.
- **Claude writes the code** (Ayush asked on 14 Oct): professional code, extensive tests, self-review, one issue per PR targeting main. Explain by tracing real data, and send progress updates as you go. Take time: brainstorm each part before coding.
- Every change goes through an issue, then a PR with `Closes #N`, then green CI.
- **Never commit company details** from his employer: no names, internal systems, code or screenshots. Use generic names.
- Next work: the open issues are the planned features (titles "Later:", "Post-v1", "Decision:", plus operations #287 and phone screens #283); the issue "Ayush's to-do list" holds the steps only he can do. `context/status.md` says which feature is next.
- Kite limits and SEBI rules drive the design (rate limits, static IP for orders, market_protection ≠ 0, 10 OPS). Re-check them before going live.

## Keep the context folder up to date (standing rule from Ayush)
Any chat can hit its limit without warning, so **don't wait to be asked**. Whenever a session changes something (an issue closed, a decision made, a PR merged, a new diagram or file), before ending your reply:
1. Update `context/status.md`: where things are and the next issue.
2. Add a short dated entry to `context/conversation-log.md`: what was asked, what was decided and why.
3. Save any new diagram as a PNG in `context/images/` and any new doc or script in `context/`.
4. Commit it on the same branch as the work, so it merges with its PR.
