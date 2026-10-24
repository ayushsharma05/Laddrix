Closes #

## What and why
<!-- What was wrong or missing, and what this changes. A flowchart helps. -->

## Checks
- [ ] Tests for every rule this changes, worked by hand where it is money or quantity
- [ ] `make check` (ruff, types, pytest, vitest, the build) is clean
- [ ] A mutation script in `context/scripts/` catches every broken rule (N/N)
- [ ] A real-browser check on the demo, if a screen changed (screenshot in `context/images/`)
- [ ] Docs updated (`docs/`), and the context files (`context/status.md`, the conversation log)
- [ ] No secret, no `.env`, nothing from an employer
