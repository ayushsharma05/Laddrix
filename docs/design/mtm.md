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
                                ─► auto-close on? closing orders (risk/closing.py), audit MTM_CLOSE
from 15:00 IST ─► DayGuards every minute: band "N intraday positions open; the broker squares
                  off in M min" ─► Positions bar: [Square off MIS (N) · 15:25] → [Confirm] →
                  closing orders for every open MIS position, audit SQUARE_OFF (who asked)
```

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
