# The server's tests

About 1,600 tests with pytest. Run them all with `make test-server` (or
`python3 -m pytest -q tests`), one file with `python3 -m pytest -q tests/test_orders.py`, and only
the tests whose names match with `-k cancel`. CI runs them on every pull request.

## How they are laid out

- **One file per part, named after it:** `test_<part>.py` tests `src/mocktrade/.../<part>.py`
  (for example `test_order_manager.py` → `orders/manager.py`). A file that tests one issue's
  behaviour across several parts is named after the behaviour (`test_spread_charts.py`,
  `test_operations.py`).
- **Every file starts with a docstring** saying what it proves, and often the gap it closed
  ("The failure before: …"). Test names are sentences: they say what the code promises.
- **Money and quantities are worked by hand** in the docstring or beside the assertion, so a
  reader can check the number without running anything.

## What the tests share

| Where | What |
| --- | --- |
| `conftest.py` | Instruments (`nifty_fut`, `reliance`, `nifty_index`), a trader and an account. Three rules for every test: the October 2026 test contracts never expire (unless a test is marked `real_expiry`), password hashing is light (fast), and the file-permission mask is put back. |
| `kite_packets.py` | Kite's binary tick packets, built exactly as Kite documents them, for the decoder and ticker tests. |
| `data/` | A small sample of Kite's instrument list. |
| `test_server.py: World` | A whole server in memory: users, accounts, fake brokers, the platform and the web app, with signed-in clients (`await world.logged_in("ayush")`). Most API tests start from it. |
| `test_stream.py: Live` | A real server on a real port with its WebSocket stream, for the stream tests. |
| `test_workspaces.py: TwoFirms` | Two firms on one server. **The leak sweep** calls every route as one firm's users and fails if anything of the other firm's shows. A new route is checked automatically. |

## Fakes, not mocks

Brokers, clocks and the network are replaced by small honest fakes (`broker/fake.py`, the
paper broker, a `Clock` with a `sleep` that moves it), passed in where the code takes them.
Patching (`monkeypatch`) is kept for the environment and a few module settings. A test that
needs time to pass moves its clock, so most never wait for real seconds.

## Beyond these tests

- **Mutation scripts** (`context/scripts/mutate-*.py`) break one rule at a time and check that a
  test here fails. Each must end "N/N caught". `test_mutants.py` checks that each mutant still matches its code
  (a stale one would quietly check nothing).
- **Browser checks** (`context/scripts/e2e-*.cjs`) drive the real screens on the demo.
- **Drills** (`context/scripts/drill-*.py`) crash, restore and roll back for real.

What each layer proves, and what must pass before a release: [docs/test/README.md](../docs/test/README.md).
