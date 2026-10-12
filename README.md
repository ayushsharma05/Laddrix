# MockTrade

MockTrade is a modular mock trading platform for experimenting with market data, order routing, risk checks, matching, account/P&L, and trading UI flows.

## Repository layout

- `docs/` – requirements, exchange notes, design docs, ADRs, test plans, and runbooks
- `common/` – shared message definitions, IDs, clock, and logging utilities
- `services/` – backend services (`mockcme`, `mds`, `appserver`, `rom`, `sor`, `accounts`, `algos`)
- `ui/` – trader-facing UI
- `tests/` – cross-component integration tests
- `deploy/` – deployment assets (e.g., Docker Compose)
- `.github/workflows/` – CI workflows

## How to run

This repository is currently scaffolded with the complete target structure.

1. Add service implementations inside `services/`.
2. Add UI code inside `ui/`.
3. Add deployment manifests inside `deploy/`.
4. Add CI workflows inside `.github/workflows/`.

As runnable code is added, keep run instructions in this README and in per-component README files.
