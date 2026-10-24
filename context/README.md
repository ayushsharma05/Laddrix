# Context for Claude (and for Ayush)

Everything a new Claude chat needs to continue this project exactly where the last one stopped. Read in this order:

1. [working-with-ayush.md](working-with-ayush.md): how he learns, how to help, and the privacy rules. **Read first.**
2. [conversation-log.md](conversation-log.md): what happened so far and why, with diagrams.
3. [status.md](status.md): where things are now and what's next.
4. Then the live state on GitHub: open issues, milestones and PRs.

## How to continue (a new chat, when Ayush says "continue")

1. Read the four files above (in order), then `CLAUDE.md` and `docs/design/product-vision.md`.
2. **Where things live:** code, design and context are all on **main**. Start new work from current main: one short-lived branch and one PR per issue, targeting main; Claude merges it once CI is green. The old stacked branches are history only.
3. Check the open PRs (CI green? review comments?) and fix anything red first.
4. Take the next item in `status.md` (the open feature issues, after Ayush's to-do issue). Work like the earlier milestones: brainstorm the traps in a table → code → tests (Python `pytest`, `ruff check .`, `ruff format --check .`; UI `cd ui && npm ci && npm run typecheck && npm test && npm run build`) → mutation check → real-browser check against the demo server (`scripts/`) → self-review → push → PR with `Closes #N` and the PR body style used in #170–#172.
5. Commit and push WIP often: limits can end a session at any time. Never run two mutation scripts at once.
6. **Update `context/` at every important point, not only at the end** (Ayush, 14 Oct): status table, Next, conversation log, screenshots. Commit context changes on the same feature branch as the code, so they merge together.
7. Before pushing, run `git status --ignored` and check no source file is hidden by `.gitignore` (K5.3 lost `ui/src/lib/format.ts` that way).

## Files

| File or folder | What it is |
| --- | --- |
| `../docs/design/product-vision.md` | **Product vision**: principles (replaceable parts, backend provides / frontend displays), feature catalogue, roadmap K8–K13 |
| `../docs/design/kite-architecture.md` | **Active design (Kite track)**: limits, components, adapter, languages, deployment |
| [images/](images/) | Every diagram from the conversation, as PNG, plus screenshots of the built screens (`k5-4-ladder.png`, `k5-5-screens.png`) |
| [scripts/](scripts/) | Real-browser checks and mutation checks Claude runs before each PR |
| [evidence/](evidence/) | Saved output of drills and live checks (the proof behind a "done") |
| [one-chart/](one-chart/) | The interactive one-chart of the architecture (open `one-chart.html` in a browser) |
| `../docs/archive/` | The paused COMEX/CME track (the CME guide, requirements, diagrams) and project history (the build map, the backlog scripts, the 15 Oct merge recovery, the old status file) |
| `../docs/running-locally.md` | How to run and test everything on one computer |

## Related repo
The paused CME track's mock exchange lives in [ayushsharma05/MockCME](https://github.com/ayushsharma05/MockCME); it is not part of the current platform ([../docs/archive/](../docs/archive/README.md)).

## Not in this folder, on purpose
The three private reference files about Ayush's firm's platform, and the raw chat (it contains company details). See the privacy rules in `working-with-ayush.md`.
