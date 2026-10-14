# MockTrade

Ayush's learning project: a personal, professional-style trading platform for **NSE/BSE on Zerodha Kite Connect** (REST for orders, WebSocket for live ticks), with a paper-trading broker that fills against live Kite prices. Since 14 Oct 2026 this **Kite track** is active; the earlier COMEX/MockCME track is paused (issues labelled `paused: cme-track`). Design: `docs/design/kite-architecture.md`.

**Before anything else, read `context/README.md`.** It has how Ayush wants to work, the full history, the current status and every diagram.

The short version:
- Ayush is new to programming and learns by doing. Use plain words, flowcharts and short answers, and explain every line of code.
- **He types the code.** For each issue give the professional code and tests, verified to run, and explain it by tracing real data line by line. You also plan, write issues and review.
- Every change goes through an issue, then a PR with `Closes #N`, then green CI.
- **Never commit company details** from his employer: no names, internal systems, code or screenshots. Use generic names.
- Next issue = the lowest open issue in the earliest open **K** milestone (K0 → K7), label `track: kite`.
- Kite limits and SEBI rules drive the design (rate limits, static IP for orders, market_protection ≠ 0, 10 OPS). Re-check them before going live.

## Keep the context folder up to date (standing rule from Ayush)
Any chat can hit its limit without warning, so **don't wait to be asked**. Whenever a session changes something (an issue closed, a decision made, a PR merged, a new diagram or file), before ending your reply:
1. Update `context/status.md`: where things are and the next issue.
2. Add a short dated entry to `context/conversation-log.md`: what was asked, what was decided and why.
3. Save any new diagram as a PNG in `context/images/` and any new doc or script in `context/`.
4. Commit it on a branch and open a PR (or add to the open context PR). Ayush merges.
If both repos were touched, update both.
