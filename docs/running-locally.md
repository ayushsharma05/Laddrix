# Running and testing MockTrade on your own computer

Everything here runs on one computer, with no broker account and no money, unless a step says
otherwise. Commands are typed in a terminal, from the repo's main folder (`MockTrade/`), unless
a step says `cd` somewhere.

```
1. install once ──► 2. the demo (play with it) ──► 3. the tests (prove it works)
                                                    ──► 4. the extra checks (optional)
                                                    ──► 5. a real server in Docker (optional)
                                                    ──► 6. live Kite prices (needs a Zerodha app)
```

## 1. Install (once)

| You need | Version | Check with |
| --- | --- | --- |
| Python | 3.12 or 3.13 | `python3 --version` |
| Node.js | 22 | `node --version` |
| git | any | `git --version` |
| Docker (only for step 5) | any recent | `docker --version` |

On Windows, use WSL (Ubuntu) and run everything inside it; the commands below are the same.

```bash
git clone https://github.com/ayushsharma05/MockTrade.git
cd MockTrade
python3 -m venv .venv && source .venv/bin/activate   # a private Python for this project
make setup                                            # the server, its tools, the test tools and the screens
```

- **`source .venv/bin/activate`:** run it again in every new terminal. Your prompt shows `(.venv)` when it is on.
- **`make setup`** runs `pip install -r requirements-dev.txt`, then `npm ci && npm run build` in
  `ui/` and `npm ci` in `ui/desktop/`. `npm ci` installs exactly the versions in `package-lock.json`.
  Every `make` command is listed at the top of the [Makefile](../Makefile).

## 2. The demo: the whole platform with simulated prices

```bash
python3 -m mocktrade.tools.demo --ui ui/dist     # leave this running
```

Open <http://localhost:8000> and sign in:

| Field | Value |
| --- | --- |
| User | `demo` |
| Password | `demo-password-123` |
| Authenticator code | in a **second** terminal: `python3 -m mocktrade.tools.demo --code` (a new code every 30 s) |

- **What you get:** a paper account, simulated NIFTY/BANKNIFTY futures, options and stocks, and
  every screen: ladder, ticket, order book, positions, algos, options, charts, alerts, research,
  admin.
- **Where its data lives:** `data/demo/`. Delete that folder to start again from nothing.
- **Stop it:** press `Ctrl+C` in its terminal.

**Replay a recorded day.** The demo records its prices. After it has run a while, the Research
screen lists the day and shows the exact command, for example:

```bash
python3 -m mocktrade.tools.demo --replay 2026-10-23 --port 8001
```

**The desktop app** (pop-out windows), with the demo running:

```bash
cd ui/desktop && npm ci && npm start          # opens http://localhost:8000 in a desktop window
```

## 3. The tests: what CI runs on every pull request

Run all of these before trusting a change. The same five jobs run on GitHub for every PR.
`make check` runs them all in one go (`make test` only the tests, `make lint` only the style
and types); the commands one by one:

```bash
# the server: about 1,600 tests, about 2 minutes
python3 -m pytest -q tests
python3 -m pytest -q tests/test_orders.py -k cancel   # one file, only tests whose names match

# style and lint (no output, or "All checks passed", means good)
ruff check .
ruff format --check .
mypy                       # the types: "Success: no issues found" means good

# the screens: about 520 tests, types, and the production build
cd ui && npm run typecheck && npm run lint && npm test && npm run build && cd ..

# the desktop shell
cd ui/desktop && npm ci && npm run typecheck && npm run lint && npm test && cd ../..
```

**CI's two other jobs, `image` and `supply-chain`, can be run too:**

```bash
docker build -t mocktrade:local . && deploy/smoke.sh mocktrade:local   # the image starts, answers, stops cleanly
pip install pip-audit && pip-audit -r requirements.lock --no-deps --disable-pip --strict   # no known-vulnerable dependency
```

## 4. The extra checks (optional, deeper)

They live in `context/scripts/`, one row each in its
[README](../context/scripts/README.md). They are slower, so they run for each change in their
area, not on every PR.

| Kind | What it proves | How |
| --- | --- | --- |
| **Mutation** (`mutate-*.py`) | The tests notice a broken rule. Each script breaks one rule at a time, runs the tests, puts the code back, and must end "N/N caught" | `python3 context/scripts/mutate-access.py`, **one at a time** (they edit files while they run) |
| **Browser** (`e2e-*.cjs`) | The real screens in Chromium: clicks, bands, pop-outs, screenshots | once: `npm install --no-save playwright && npx playwright install chromium`; then, with the demo running, `node context/scripts/e2e-ladder.cjs` |
| **Drills** (`drill-*.py`) | Recovery for real: a crash and restart, a backup and restore, a stolen credential, a deploy and rollback | `python3 context/scripts/drill-credential.py` |
| **Measurements** | Speed and lateness, with numbers | `PYTHONPATH=src python3 context/scripts/measure-workload.py` |

If a mutation script is stopped half way, run `git status`. Any changed file under `src/` was
being mutated: `git checkout -- <file>` puts it back. **Only do this when no work of yours is
unsaved in that file.**

## 5. A real server in Docker (paper trading)

This is the production setup on your own computer: HTTPS through Caddy, a locked-down
container, and your own users. Prices are simulated.

```bash
cp deploy/.env.example deploy/.env          # then set MOCKTRADE_DOMAIN=localhost in it
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users new-key
#   copy the printed MOCKTRADE_SECRET_KEY=... line into deploy/.env (and into your password manager)
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml run --rm -it app python -m mocktrade.tools.users add-user ayush "Ayush" --roles admin,trader
#   it prints an otpauth:// link once: open it as a QR code in an authenticator app; it asks for a password
docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users add-account acc-paper paper PAPER1 --owner ayush
docker compose -f deploy/compose.yaml restart app
```

Open <https://localhost>; the browser warns once about Caddy's local certificate. All the
settings are listed at the top of `src/mocktrade/tools/serve.py`. The operator's tools
(`users`, `backup`, `audit`) are described in [runbooks/](runbooks/README.md).

## 6. Live Kite prices (needs your Zerodha Kite Connect app)

Only with your own Kite Connect app (API key and secret). **Never commit them.** Orders on a
`kite` account are real: keep to paper accounts until the go-live checklist is done
([runbooks/go-live.md](runbooks/go-live.md)).

1. In Kite's developer console, set the app's redirect URL to `<MOCKTRADE_ORIGIN>/kite/callback`.
2. In `deploy/.env`, add `MOCKTRADE_PRICES=kite`, `KITE_API_KEY=...` and `KITE_API_SECRET=...`.
3. Add a Kite account (`users add-account acc-me kite <your Zerodha client id> --owner ayush`), plus
   a paper account for the same user. The paper one fills against your live prices.
4. Optional, before real orders: the exchange's freeze quantities (the most one order may carry).
   Make a file of `underlying,qty` lines (for example `NIFTY,1800`) from NSE's latest freeze
   list and set `MOCKTRADE_FREEZE_FILE` to it (`deploy/.env.example` shows how). The demo
   has sample ones built in.
5. Restart, sign in, and press **Log in to Kite** on the band. You do this once a day; the
   morning routine is in [runbooks/morning.md](runbooks/morning.md).

To watch raw prices in a terminal instead:

```bash
python3 -m mocktrade.tools.login                    # Kite login, once a day
python3 -m mocktrade.tools.watch NFO:NIFTY26OCTFUT  # a live ladder in the terminal
```

## When something goes wrong

| You see | Do |
| --- | --- |
| `ModuleNotFoundError: mocktrade` | the virtual environment is off: `source .venv/bin/activate` |
| the page at :8000 shows only an API, no screens | the screens aren't built: `cd ui && npm run build` |
| `address already in use` | another demo is still running: stop it, or use `--port 8010` |
| "another MockTrade server owns data/demo" | one is already running on that folder: stop it first (one server per data folder, by design) |
| the login code is refused | codes last 30 s: get a fresh one with `--code` |
| a browser check can't find Chromium | `npx playwright install chromium`, or set `CHROMIUM_PATH` |
