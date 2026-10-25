# Test plan

What each layer of checks proves, how to run it, and what must pass before a release. Every change goes through a PR with these checks; the scripts live in `context/scripts/`, described one per row in `context/scripts/README.md`.

## The layers

| Layer | What it proves | How to run | Runs in CI |
|---|---|---|---|
| **Python tests** (`tests/`, about 1,480) | Each part's rules on its own and wired together: domain rules, the order manager, risk, recovery, the paper and Kite adapters (against a fake Kite), the REST API, backups | `pytest` (all), `pytest tests/test_x.py -k name` | yes (`test`) |
| **UI tests** (`ui/src/**/*.test.ts(x)`, about 420) | Widgets and models: what the screen shows for given data, what a click sends, keyboard shortcuts, themes and contrast | `cd ui && npm test` | yes (`ui`) |
| **Lint, format, types** | Style, the server's types (mypy), the screens' types and ESLint (React's hooks rules) | `ruff check .` · `ruff format --check .` · `mypy` · `cd ui && npm run typecheck && npm run lint` | yes |
| **Build and image** | The UI builds; the Docker image builds and passes its smoke test (health, a clean stop) | `cd ui && npm run build` · `deploy/smoke.sh` | yes (`ui`, `image`) |
| **Desktop shell** | The desktop app's pop-out windows | `cd ui/desktop && npm test` | yes (`desktop`) |
| **Mutation scripts** (`mutate-*.py`, 60+) | That the tests notice when a rule is broken. Each breaks one rule at a time, runs the tests and restores the file; a surviving mutant is a missing test | `python3 context/scripts/mutate-<area>.py` (alone) | no: run for each change in its area |
| **Browser checks** (`e2e-*.cjs`, 34) | The real screens in Chromium against the demo: clicks, bands, pop-outs, screenshots | `python3 -m laddrix.tools.demo --ui ui/dist`, then `node context/scripts/e2e-<area>.cjs` | no: run for each UI change in its area |
| **Demo checks** (`check-*.py`) | A feature end to end over the real API on the demo | `python3 context/scripts/check-<area>.py` | no |
| **Drills** | What must survive a bad day: a crash (`drill-restart.py`, plus `e2e-restart-bands.cjs`) and a backup restored into a fresh folder (`drill-backup-restore.py`) | each runs its own demo | no: before a release |
| **Go-live, paper** | Every go-live step that can be shown on paper (`golive-paper.py`). Never evidence about Kite | `python3 context/scripts/golive-paper.py` | no: before a release |
| **Deploy and rollback** | The real `deploy.sh` and `rollback.sh` with real images and a data volume on a staging copy: refused in market hours, deployed, rolled back, a bad release rolled back by itself, an emergency deploy logged, data restored (`drill-deploy-rollback.sh`) | `context/scripts/drill-deploy-rollback.sh <old-ref> <new-ref>` (needs Docker) | no: before a release |
| **Go-live, live** | The same steps against Kite, by Ayush, with one share and his go-ahead | [go-live.md](../runbooks/go-live.md) | never automated |

Two rules hold for every layer:
- **A test that reads the audit trail flushes it first** (`await log.flush()`): it is written in the background.
- **After every merge, check `main`'s CI.** A race can pass on a PR and fail on `main`.

## Scenarios that must pass before a release

1. **CI is green on `main`:** Python, UI, desktop, image.
2. **Orders:** place, partial fill, modify, cancel, reject by rules, by risk and by the broker, and an unconfirmed outcome settled by reconcile. These are covered by `tests/test_order_manager.py`, `tests/test_kite_broker.py`, `golive-paper.py` and the [order life story](../design/order-life.md).
3. **Safety:** STOP blocks new orders and passes cancels; the daily-loss stop; MTM limits; square-off; the price band; the throttle (`tests/test_risk_checks.py`, `tests/test_server.py`, `tests/test_mtm.py`, `tests/test_throttle.py`).
4. **Restart and reconnect:** `drill-restart.py` 17/17 and `e2e-restart-bands.cjs`: the position rebuilt, every fill counted once, UNPROTECTED and PRICES OLD shown and cleared.
5. **Backup and restore:** `drill-backup-restore.py` 19/19 on the release's code.
6. **Replay a recorded day,** including a non-demo instrument: `e2e-recording-instruments.sh`.
7. **The screens:** the browser checks for the areas the release touched, and screenshots of the ladder, ticket and order book in each theme.
8. **Go-live:** `golive-paper.py` 19/19. For a first live release, Ayush ticks the live boxes in [go-live.md](../runbooks/go-live.md).
9. **Docs match the code:** the design docs, runbooks, glossary and release notes are updated, and known limitations are listed.

The release steps themselves (version, notes, tag, deploying outside market hours, rollback) are in [release.md](../runbooks/release.md).
