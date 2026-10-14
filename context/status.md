# MockTrade status and next steps

As of 12 Oct 2026. Check GitHub for the live state. Issues are the source of truth.

## Where things are
- Phase 0 (setup), Phase 1 (trader → product brief) and Phase 2 (hard numbers, folded into the brief) are done.
- Phase 3 (study CME): #14, #15 and #16 are closed. Next: **#17** product facts for GC/SI/HG, **#18** one day of Databento data, **#19** ADR-0001 products.
- Phases 4–8 (business model, architecture, contracts, failures, test strategy) are open.
- Releases v0.1–v1.0 are open. The exchange part now lives in the MockCME repo.
- **Parallel track:** most coding currently happens in **MockCME** (start at MockCME #1 → #4).

## How to find the next issue
The earliest open milestone, then the lowest open issue number in it. The build map's "you are here" bar shows the same thing.

## Small open items from reviews
- Reopen #7 and finish it: `tests/test_smoke.py` still uses 2-space indentation (should be 4), and `requirements-dev.txt` has no trailing newline.
- Delete the stale branch `4-create-the-github-projects-board`.
- Use descriptive PR titles. End every file with a newline. Comment when closing an issue that has no code.
- Issues labelled `detail-later` get full steps written when he reaches them.

## Refreshing the build map
`build-map/` holds the generator. Re-fetch issue states into `issues.tsv` (number, state, milestone, title; use `moved` for the 6 moved issues), update `cme_next.tsv`, run `python build_map.py`, then republish the HTML.

## Tool limits seen in Claude's GitHub access
- GraphQL is blocked, so Projects boards and issue transfers aren't possible. Ayush does those himself.
- Repository settings can't be changed (e.g. auto-delete head branches), and new repos can't be created. Ayush does those himself.
- The REST API for issues, labels, milestones, comments and PRs works.
