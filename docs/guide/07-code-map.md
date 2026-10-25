# Chapter 7 · The code, folder by folder

[Guide home](README.md) · previous: [Chapter 6](06-screens.md) · next: [Chapter 8 · Data, tests and running it](08-data-tests-running.md)

---

## 7.1 How to read a Python file in this project (5 minutes)

Every file starts with a **docstring**: a comment in `"""…"""` saying what the file is for,
often with a small drawing. **Always read that first.** Then:

```python
class OrderManager:                      # a "class" is a blueprint for a thing with a job
    def __init__(self, account_id, ...): # how to build one: what it needs to start
        self.account_id = account_id     # "self.x" = something this thing remembers

    async def submit(self, request):     # a "method": something it can do
        ...                              # "async": it may wait (for the broker) without
                                         #  freezing everything else
```

| You see | It means |
| --- | --- |
| `x: int = 0` | x is a whole number, starting at 0 |
| `str \| None` | text, or nothing |
| `list[OrderView]` | a list of order views |
| `dict[str, int]` | a lookup table: text → number |
| `@dataclass(frozen=True)` | a bundle of facts that can't change after it is made |
| `Enum` | one of a fixed set of choices |
| `raise X("why")` | stop here and report a problem |
| `try: … except X:` | try this; if problem X happens, do that instead |
| `# fmt: skip` | "formatter, leave my line layout alone" (only about looks) |

**A tip for big files:** don't read top to bottom. Search for the method you care about
(`def submit`) and read that one.

## 7.2 The server: `src/mocktrade/`, every folder

### The building plan and the base

| File | Its job, in one line |
| --- | --- |
| `platform.py` | Builds and connects everything for each account (start here: `add_account`). |
| `recovery.py` | After a restart, keeps trading shut until orders and positions are rebuilt. |
| `config.py` | Reads settings from the environment (and a local `.env` file). |
| `metrics.py` | Measures how long each step takes, so speed is a number, not a guess. |
| `version.py` | The version number (1.0.0-rc.1) and which commit it was built from. |
| `logsafe.py` | Hides secrets in every log line before it is written. |

### `domain/`: the words, no logic

| File | Its job |
| --- | --- |
| `money.py` | Money as whole paise; turns rupees to paise and back; prints "₹56,27,250.00". |
| `instruments.py` | What an instrument is: exchange, symbol, lot size, tick, expiry, strike. |
| `orders.py` | Orders: side, type, product, status, OrderRequest, OrderUpdate, fills. |
| `order_spec.py` | What order types an instrument allows, published to the screens. |
| `ticks.py` | A tick: last price and depth. |
| `accounts.py` | Users, roles (trader, risk, admin), broker accounts, who may see or trade what. |
| `gtt.py`, `portfolio.py` | Shapes for GTT triggers, funds, holdings and trades. |

### `refdata/`: the list of what can be traded

| File | Its job |
| --- | --- |
| `store.py` | Every instrument by our id, plus each broker's id for it. |
| `expiry.py` | Expiry day: when a contract ends, how it settles, what may still be ordered. |
| `freeze.py` | The exchange's freeze quantity: the most one order may carry. |

### `orders/`: the kitchen manager

| File | Its job |
| --- | --- |
| `manager.py` | **The heart.** Tracks every order: submit, modify, cancel, apply updates, unanswered orders, restart. |
| `gate.py` | The chain every order, change and cancel must pass: risk → margin → throttle. |
| `model.py` | The order manager's own words: events (SUBMITTED, FILLED…), pending states, order ids (MT1, MT2…). |
| `codec.py` | Turns orders into plain text (JSON) for the ledger and the API, and back. |
| `basket.py` | Several orders sent together after one check of them all. |
| `gtt.py` | GTTs: placed at Zerodha, watched, and adopted when they fire. |
| `trade_check.py` | Checks our fills against the broker's own trade list. |

### `risk/`: the safety inspector

| File | Its job |
| --- | --- |
| `checks.py` | Every per-order check (limits, price band, lot, loss, double click, reduce-only, STOP). |
| `limits.py`, `limits_spec.py` | The numbers (biggest order, daily loss…) and how the admin screen edits them. |
| `kill_switch.py` | STOP: on first, then cancel everything. |
| `loss_watch.py` | Turns STOP on by itself at the daily loss limit. |
| `throttle.py` | At most 8 orders a second (Kite allows 10), measured over a sliding second. |
| `margin.py` | Refuses an order your funds can't carry. |
| `closing.py` | The exit desk: closes one position safely. |
| `flatten.py` | Flatten, and the STOP report. |
| `mtm.py` | Your own loss limits, and what each did (the breach record). |
| `squareoff.py` | The square-off warning and the one-click close. |
| `access.py` | Who may act on an account right now (re-checked when a delayed action runs). |
| `surveillance.py` | Patterns worth a look: self-trades, large orders, cancel ratios, bursts. |

### `broker/`: the supplier's phone

| File | Its job |
| --- | --- |
| `base.py` | **The plug shape**: what every broker must be able to do. |
| `paper.py` | The pretend broker: fills against live prices, queues, delays. |
| `paper_funds.py` | Pretend money and margin, worked out like a real broker. |
| `rules.py` | Rules every broker applies the same way, so paper is never more lenient than real. |
| `fake.py` | A scripted broker for tests. |
| `kite/http.py`, `kite/orders.py` | Talking to Kite's REST API; turning Kite's order answers into ours. |
| `kite/broker.py` | The Kite broker, built from the pieces below. |
| `kite/ticker.py`, `kite/decoder.py`, `kite/feed.py`, `kite/encode.py` | Kite's live price line: keeping it open, reading its bytes, building them for tests. |
| `kite/session.py`, `kite/daily_login.py`, `kite/token_store.py` | The daily Kite login and keeping its token. |
| `kite/instruments.py` | Downloading Kite's list of instruments. |
| `kite/gtt.py`, `kite/portfolio.py` | GTTs, funds and holdings at Kite. |
| `kite/live.py` | Each Kite account's connection inside the server, from start-up until 6 AM. |

### `marketdata/`: the notice board

| File | Its job |
| --- | --- |
| `service.py` | Holds the latest tick of each instrument; tells subscribers. |
| `simulator.py`, `sim_universe.py` | Made-up prices for the demo, and which contracts they cover. |
| `vap.py` | Volume at each price today (a ladder column). |
| `prints.py` | Time and sales: the individual trades. |
| `movers.py` | Biggest gainers and losers. |
| `ladder.py` | A text ladder for the terminal. |
| `sources.py`, `markets.py` | Whose prices each account uses (each trader's own Zerodha prices), and what the screens read from them. |

### `positions/`: the accountant

| File | Its job |
| --- | --- |
| `book.py` | Positions from fills: quantity, average price, realised and unrealised P&L, per product. |
| `reconcile.py` | Compares our positions with Zerodha's; raises a band on a real difference. |
| `external.py` | Trades made outside MockTrade (in Zerodha's own app), and the mismatch they cause. |

### `algos/`: the robot helpers

| File | Its job |
| --- | --- |
| `engine.py` | Runs every algo: gives it prices and timers, sends its child orders, stops it safely. |
| `spec.py`, `builtin.py` | What settings each algo needs (the screens are built from this), and the list of algos. |
| `slicer.py` | Splits a big order into small ones over time. |
| `iceberg.py` | Shows a big order a little at a time. |
| `schedule.py` | TWAP and VWAP: trade to a schedule over a window. |
| `pov.py` | Trade a set share of what the market trades. |
| `spreader.py` | Trade a two-leg spread as one order. |
| `execution.py` | What these execution algos share. |
| `protect.py` | Protection: trailing stop, OCO (target or stop, one cancels the other), bracket. |
| `autospreader.py` | The Autospreader (ASE): quotes a spread's legs and hedges every other leg the moment one fills. |

### The rest

| Folder | Its job |
| --- | --- |
| `options/` | Option prices and Greeks (`pricing.py`, `model.py`), the chain, multi-leg strategies. |
| `charges/` | Brokerage, taxes and fees per fill, from published schedules. |
| `charts/` | Candles, and where past candles come from. |
| `alerts/` | Price alerts per user, saved to disk; signed webhooks. |
| `spreads/` | Spread definitions of 2 to 6 legs (`model.py`), saved per user (`store.py`), priced live from their legs (`pricer.py`). |
| `structures/` | The spreads an account holds (`book.py`): fills allocated to them, their P&L, what is left over. |
| `portfolio/` | Funds, holdings and tradebook, as the broker reports them. |
| `reports/` | End-of-day reports, history over many days, CSV and PDF. |
| `research/` | Recording ticks, replaying a day, backtests on recorded days. |
| `ops/` | Alerts (the bands), the scheduler (jobs at India times), operational checks, the day guards, lost stops, sending alerts off the machine; the supervisor that restarts failed loops, one server per data folder (`fence.py`), the status of every part (`status.py`) and JSON logs (`logs.py`). |
| `store/events.py` | The ledger: every event into SQLite (`events.db`), written in the background so trading never waits for the disk. |
| `server/` | `app.py` puts the API together from `routes/` (one file per area: `session`, `ops`, `market`, `trading`, `portfolio`, `risk`, `spreads`, `research`), `context.py` the checks every route shares (who is asking, which account, which firm), `bodies.py` the request bodies, `stream.py` the live line, `security.py` passwords and codes, `users.py` the users database, `admin.py` the admin API, `formats.py` shared JSON shapes. |
| `tools/` | Programs you run: `serve` (real), `demo`, `users` (add users), `backup`, `restore`, `login` (Kite), `watch` (a terminal ladder), `amo_check` (one go-live test order), `loadtest`, `audit` (read and check the audit trail). |

## 7.3 The screens: `ui/src/`

| Folder | Its job |
| --- | --- |
| `main.tsx`, `App.tsx` | The starting point: login, then the shell. |
| `app/` | Puts the app together (`createApp`): the REST client, the stream, the store. |
| `api/` | `rest.ts` (requests), `stream.ts` (the live line), `types.ts` (the shape of everything the server sends). |
| `state/` | `store.ts` (the one box with everything), the session, the order specs, the instrument list. |
| `shell/` | The frame: toolbar, bands, STOP dialog and band, login, themes, version. |
| `workspace/` | Docking, tabs, layouts, links, pop-outs, and the widget registry. |
| `widgets/` | One folder per widget ([Chapter 6](06-screens.md)). |
| `components/`, `lib/`, `keys/`, `theme/`, `styles/` | Shared pieces, formatting, keyboard shortcuts, themes, styles. |
| `test/` | Fakes and sample data for the screen tests. |

**How to read a React file:** a component is a function that returns what to draw.

```tsx
export function Flatten({ count }: { count: number }) {   // a component, given a count
  const [armed, setArmed] = useState(false);              // something it remembers
  return (                                                 // what to draw:
    <button onClick={() => setArmed(true)}>                //   a button that, when clicked…
      {armed ? "Confirm" : `Flatten (${count})`}           //   …changes its own words
    </button>
  );
}
```

That is the whole idea behind the "two clicks" buttons in MockTrade: the first click only
"arms" it, and the second does the action.

## 7.4 The tests sit next to the code

```
 src/mocktrade/risk/checks.py   ◄── tested by ──  tests/test_risk_checks.py
 ui/src/shell/StopBand.tsx      ◄── tested by ──  ui/src/shell/StopBand.test.tsx
```

Test names are sentences, such as `test_a_loss_limit_and_a_square_off_at_once_close_the_position_once`.
Reading the test names of a file is often the fastest way to learn what the code promises.

---

Next: [Chapter 8 · Data, tests and running it](08-data-tests-running.md).
