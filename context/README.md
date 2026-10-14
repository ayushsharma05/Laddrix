# Context for Claude (and for Ayush)

Everything a new Claude chat needs to continue this project exactly where the last one stopped. Read in this order:

1. [working-with-ayush.md](working-with-ayush.md): how he learns, how to help, and the privacy rules. **Read first.**
2. [conversation-log.md](conversation-log.md): what happened so far and why, with diagrams.
3. [status.md](status.md): where things are now and what's next.
4. Then the live state on GitHub: open issues, milestones and PRs.

## How to continue (a new chat, when Ayush says "continue")

1. Read the four files above (in order), then `CLAUDE.md` and `docs/design/product-vision.md`.
2. **Where things live:** code, design and context are integrated on **main** through K9.1. Start new work from current main; create a short-lived branch and a PR targeting main. The old stacked branches are historical references, not the next starting point.
3. Check the open PRs (CI green? review comments?) and fix anything red first.
4. Take the "Next" item in `status.md`. Work like the earlier milestones: brainstorm the traps in a table → code → tests (Python `pytest`, `ruff check .`, `ruff format --check .`; UI `cd ui && npm ci && npm run typecheck && npm test && npm run build`) → mutation check → real-browser check against the demo server (`scripts/`) → self-review → push → PR with `Closes #N` and the PR body style used in #170–#172.
5. Ayush allowed pushing straight to these milestone branches (14 Oct). Commit WIP often: limits can end a session at any time.
6. **Update `context/` at every important point, not only at the end** (Ayush, 14 Oct): status table, Next, conversation log, screenshots. Commit context changes on the same feature branch as the code, so they merge together.
7. Before pushing, run `git status --ignored` and check no source file is hidden by `.gitignore` (K5.3 lost `ui/src/lib/format.ts` that way).

## Files

| File or folder | What it is |
| --- | --- |
| `../docs/design/product-vision.md` | **Product vision**: principles (replaceable parts, backend provides / frontend displays), feature catalogue, roadmap K8–K13 |
| `../docs/design/kite-architecture.md` | **Active design (Kite track)**: limits, components, adapter, languages, deployment |
| [cme-wiki-guide.md](cme-wiki-guide.md) | Beginner guide to CME MDP 3.0 (prices) and iLink 3 (orders), with 3 diagrams |
| [build-map/](build-map/) | The MockTrade Build Map: `MockTrade-Build-Map.html` (open in a browser) and the generator that rebuilds it |
| [images/](images/) | Every diagram from the conversation, as PNG, plus screenshots of the built screens (`k5-4-ladder.png`, `k5-5-screens.png`) |
| [scripts/](scripts/) | Real-browser checks and mutation checks Claude runs before each PR |
| [backlog-scripts/](backlog-scripts/) | The Python scripts that created all issues, labels and milestones in both repos |

## Related repo
The mock exchange lives in [ayushsharma05/MockCME](https://github.com/ayushsharma05/MockCME). It has its own `context/` folder.

## Not in this folder, on purpose
The three private reference files about Ayush's firm's platform, and the raw chat (it contains company details). See the privacy rules in `working-with-ayush.md`.
