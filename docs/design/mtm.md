# MTM limits and the square-off assistant (K10.5, #217)

Two guards against the day going wrong:
- **MTM limits:** your own loss limits, per position and for the account.
- **The square-off assistant:** closes intraday (MIS) positions before the broker does it for you.

```
position or price changes ─► MtmWatch.check (risk/mtm.py)
    position rule:  P&L today ≤ −₹limit, or loss ≥ limit% of the position's value
    portfolio rule: day P&L ≤ −stop, or ≥ +target
  reached (once a day per rule) ─► stream "mtm" → Messages; band alert (ops/guards.py)
                                ─► audit MTM_BREACHED
                                ─► auto-close on? the exit desk (risk/closing.py), audit MTM_CLOSE
from 15:00 IST ─► DayGuards every minute: band "N intraday positions open; the broker squares
                  off in M min" ─► Positions bar: [Square off MIS (N) · 15:25] → [Confirm] →
                  the exit desk for every open MIS position, audit SQUARE_OFF (who asked)
```

## Coordinated exits (#261)

Several things can close one position: a loss limit, the square-off, a stop algo, a target
the trader left resting, the trader's own click. Before #261 each sized its close from the
position it last saw, so two of them at once could close it twice and leave the account
**short where it was long** (or the other way round).

- **Every closing order is marked reduce-only:** the desk's closes, and the exits of stops,
  OCO targets and bracket targets (never a bracket's entry). The risk gate refuses a
  reduce-only order that would close more than is open in its product, counting the other
  reduce-only orders still working on that side, whatever their state (in doubt included):
  "would close more than is open: 150 open in MIS, 75 already being closed".
- **One exit desk per account** handles every forced close (loss limits and square-off):

```
lock this position (instrument + product): a second forced close waits, then sees what's left
  ─► stop the algos on it ("forced close: …"): their stops would race, entries would reopen
  ─► cancel the resting orders that would also close it, and wait up to 3 s for them
     (a fill meanwhile is fine: it is in the position)
  ─► read the position now; take off what closing orders still working will close
     (a cancel that didn't take; an order whose fate is unknown is never cancelled blind)
  ─► send closing orders for the rest only; say what it left: "75 left to orders already
     closing it", in the audit trail (MTM_CLOSE / SQUARE_OFF) and the square-off message
```

- **Stop algos size their exit to what is still open:** a trailing stop, OCO or bracket stop
  that fires after something else closed the position exits only the rest, and if nothing is
  left it finishes saying "the position is already closed (another exit got there first)".
- **A plain sell is the trader's own** (it may be meant to open a short): the risk gate doesn't
  limit it. The desk still cancels it before a forced close, since it would close too.
- **The order book marks closes** ("SELL close"), so a person sees which orders can't reverse
  anything.

## Rules (each a real failure)

- **Closing goes through the normal path:** risk, margin, throttle and audit, never around them.
  - Exits always pass: risk allows closing trades past the daily loss limit, and the margin
    check never asks about an exit.
  - A close is a MARKET order with the broker's automatic protection, split into pieces no
    bigger than the per-order lot limit.
  - All pieces share one basket id ("MTM…" / "SQ…"). The double-click guard lets identical
    legs of one basket through, so a split close isn't refused as a double click. Reports
    name these baskets "MTM stop" and "square-off".
- **Alert by default, close only when asked:** auto-close is the trader's choice for each rule.
- **Once a day per rule:** a limit that was reached doesn't fire on every tick. Setting a new
  limit watches it afresh.
- **Rules survive a restart:** they are kept as JSON in `<data>/rules/mtm-<account>.json`, and
  every change is audited (MTM_RULE). A file that can't be read loads no rules and logs an error.
- **Cut-off times are data, published by the server:** `risk/squareoff.py` `CUTOFFS`, with
  equity (NSE/BSE) at 15:20 and F&O (NFO/BFO) at 15:25 IST, checked 15 Oct 2026. Re-check
  before trading real money; brokers change them.
- **One click, one confirmation:** the button only arms on the first click, and a confirmation
  applies only to what was shown (it resets when the positions change).
- **The square-off closes only MIS positions.** Overnight (NRML) positions stay.
- **The band clears itself:** an MTM alert clears once its position (for the portfolio, every
  position) is flat. The square-off alert clears when no MIS position is left or the window ends.

## API

| | |
|---|---|
| `GET /api/accounts/{a}/mtm` | rules, and whether each was reached today |
| `PUT …/mtm/positions` `{instrument_id, product, max_loss_paise?, max_loss_pct?, auto_close}` | set a position limit (problems are named in words) |
| `DELETE …/mtm/positions?instrument_id=&product=` | remove it |
| `PUT / DELETE …/mtm/portfolio` `{max_loss_paise?, target_paise?, auto_close}` | the portfolio stop |
| `GET …/square-off` | cut-offs, the window, open MIS positions with the minutes left |
| `POST …/square-off` `{confirm: true}` | close every open MIS position |

## Screens

- **MTM Limits** (a tab beside Positions):
  - portfolio stop and target, with auto-close;
  - each open position with its P&L, a limit in ₹ or %, auto-close, and its rule, including whether it was reached today.
- **Positions bar:** "Square off MIS (n) · 15:25" when MIS positions are open. It turns red inside
  the window, and the hover lists the positions and the minutes left.

Checks:
- `tests/test_mtm.py` (a simulated day);
- `ui/src/widgets/mtm/mtm.test.tsx`;
- `context/scripts/mutate-mtm.py` (18/18);
- real browser `context/scripts/e2e-mtm.cjs`.
