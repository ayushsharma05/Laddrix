# CI

`ci.yml` runs on every pull request, on every push to `main`, and on a release tag (`v*`).
Every job must be green before a merge.

| Job | What it checks |
| --- | --- |
| `test` | The server: installs the locked packages (with hashes), then ruff (lint and format), mypy (the types) and every pytest test. |
| `ui` | The screens: `npm audit`, the type check, every vitest test, and the production build. |
| `desktop` | The desktop app: `npm audit`, the type check and its tests (without starting Electron). |
| `image` | The deploy files: shellcheck on `deploy/*.sh`, the Docker image builds, the Compose file is valid, and `deploy/smoke.sh` (the image starts locked down, becomes ready, answers, logs JSON and stops cleanly). |
| `supply-chain` | No known vulnerability in the Python packages (pip-audit), no secret anywhere in the history (gitleaks), and the SBOMs of the server, the screens and the desktop app (kept with each run; a release keeps its own). |

The same checks on your own computer: `make check` (and `make image` for the Docker part).
