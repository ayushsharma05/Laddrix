# Contributing to Laddrix

How a change gets made here. The project is a trading platform, so every change must show
that it is right. Saying so is not enough.

```
an issue ──► a branch ──► the change + its checks ──► a pull request ("Closes #N") ──► green CI ──► merge
```

## 1. Start from an issue

Every change starts as a GitHub issue that says what is wrong or missing, and when it is
done. One issue becomes one pull request. A large piece of work is split into child issues
first (for example the Autospreader: #414–#416, then #424–#429).

## 2. Make the change, with its checks

| Check | What it proves | Where |
| --- | --- | --- |
| **Tests** | each rule the change touches, worked by hand where it is money or quantity | `tests/` (server), `*.test.ts(x)` beside the code (screens) |
| **Lint, format, types** | the code is clean and consistent | `make lint` |
| **Mutation script** | the tests notice when a rule breaks: it breaks each rule in turn and must end "N/N caught" | `context/scripts/mutate-*.py` |
| **Browser check** | the real screens work: real clicks on the demo, with a screenshot | `context/scripts/e2e-*.cjs` |
| **Docs** | the design and the runbooks say what the code now does | `docs/` |

`make check` runs what CI runs (except the Docker image: `make image`). How to set up and
run each tool: [docs/running-locally.md](docs/running-locally.md).

## 3. Open the pull request

- Put `Closes #N` at the top. The template lists the checks.
- **CI must be green:** the server's tests and lint, the screens' lint, tests and build, the
  desktop app, the Docker image's smoke test, and the supply-chain checks (known
  vulnerabilities, secrets in the history, SBOMs).
- Merge with a merge commit, never a squash.

## Rules that are never bent

- **Never commit a secret** (API keys, passwords, `.env` files, broker tokens). CI scans the
  whole history for them.
- **Never commit anything from an employer:** no names, systems, code or screenshots. Use
  generic names.
- **Never name or copy another vendor's product.** Laddrix is its own product.
- **The backend provides, the frontend displays.** Market rules (lot sizes, order types,
  cut-offs, why an order was refused) come from the server. The screens hard-code none.
- **No live order** without the go-live checklist ([docs/runbooks/go-live.md](docs/runbooks/go-live.md)).
- **Every component sits behind an interface** (broker, prices, charges, storage), so each can be
  replaced without touching the core.

## Where things are

| Folder | What |
| --- | --- |
| `src/laddrix/` | the server ([code map](docs/guide/07-code-map.md)) |
| `ui/` | the screens (React); `ui/desktop/` the desktop app (Electron) |
| `tests/` | the server's tests ([README](tests/README.md)) |
| `deploy/` | Docker, Caddy, the deploy scripts, monitoring |
| `docs/` | the guide, design docs, runbooks, roadmap ([index](docs/README.md)) |
| `context/` | the project's memory: status, the decision log, scripts, screenshots, evidence ([README](context/README.md)) |
