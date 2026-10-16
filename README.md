# MockTrade

**New here? Start with [the guide](docs/guide/README.md):** the whole project from zero, in plain words, with pictures and flowcharts (one-page summary: [docs/GUIDE.md](docs/GUIDE.md)).

A multi-user, broker-agnostic trading platform for NSE and BSE, designed the way a professional trading-platform team would build it: a price ladder, order blotter, positions and P&L, audit trail and parent/child algo management, with a paper broker that fills against live prices.

The first broker is **Zerodha Kite Connect**. Every broker plugs in behind one adapter interface, so others (or direct exchange access) can be added without changing the core.

- **Architecture:** [docs/design/kite-architecture.md](docs/design/kite-architecture.md) and the one-chart in [context/one-chart/](context/one-chart/)
- **Plan:** GitHub issues with the `track: kite` label, milestones K0 to K7

## Run the tests

```
python -m venv .venv
.venv\Scripts\activate        # Windows (Mac/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
ruff check .
pytest
```

## Code layout

```
src/mocktrade/
  domain/   shared types: money in paise, instruments, users and accounts, orders
  broker/   the broker interface and its adapters (fake, paper, kite)
tests/      one test file per module
```

More packages (market data, order manager, risk, positions, algos, server) arrive milestone by milestone.
