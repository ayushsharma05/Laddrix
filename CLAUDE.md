# MockTrade

Ayush's learning project: a professional-style trading platform for COMEX metals futures (GC, SI, HG, plus multi-leg spreads) that trades against a mock exchange replaying real data. The exchange itself is the separate repo `ayushsharma05/MockCME`.

**Before anything else, read `context/README.md`.** It has how Ayush wants to work, the full history, the current status and every diagram.

The short version:
- Ayush is new to programming and learns by doing. Use plain words, flowcharts and short answers, and explain every line of code.
- **He writes the code.** You plan, write issues, review and coach.
- Every change goes through an issue, then a PR with `Closes #N`, then green CI.
- **Never commit company details** from his employer: no names, internal systems, code or screenshots. Use generic names.
- Next issue = the earliest open milestone, then the lowest open number.

## Keep the context folder up to date (standing rule from Ayush)
Any chat can hit its limit without warning, so **don't wait to be asked**. Whenever a session changes something (an issue closed, a decision made, a PR merged, a new diagram or file), before ending your reply:
1. Update `context/status.md`: where things are and the next issue.
2. Add a short dated entry to `context/conversation-log.md`: what was asked, what was decided and why.
3. Save any new diagram as a PNG in `context/images/` and any new doc or script in `context/`.
4. Commit it on a branch and open a PR (or add to the open context PR). Ayush merges.
If both repos were touched, update both.
