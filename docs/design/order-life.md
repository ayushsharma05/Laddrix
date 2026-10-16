# An order's life, and the key journeys

How an order moves from a click to its end, and what happens in the moments that matter: a partial fill, a modify, a cancel, a reject, a restart, the broker disconnecting, and STOP. The code is `src/mocktrade/orders/manager.py`; the words are in the [glossary](glossary.md). The happy path (place and fill) is drawn in [kite-architecture.md](kite-architecture.md).

## States

![Order states](../../context/images/v1-order-life.png)

An order's status **only moves forward**. The allowed moves are `ALLOWED` in `orders/manager.py`:

| From | Can move to | What the trader sees |
|---|---|---|
| (new) | REJECTED, never sent: refused by our rules (lot, tick, instrument, account), the risk gate or the throttle | REJECTED, with the reason |
| PENDING | OPEN, PARTIAL, FILLED, CANCELLED, REJECTED | sent; waiting for the broker to confirm |
| OPEN | PARTIAL, FILLED, CANCELLED, REJECTED | working at the exchange |
| PARTIAL | FILLED, CANCELLED | partly filled; the rest still works |
| FILLED, CANCELLED, REJECTED | nothing (final) | done; a cancelled order keeps what had filled |

Next to the status, `pending` says what we're waiting on:
- **NONE**: nothing.
- **UNCONFIRMED**: the place call got no clear answer (a timeout or a dropped connection). The order is never assumed rejected, and its risk stays counted. The broker's order list settles it (#259):
  - the manager asks the broker again by itself, at 2, 5, 10, 20, 30, 60 and 60 seconds, and after a restart too;
  - one list without the order proves nothing (a slow broker lists it late);
  - it becomes REJECTED only when **3 lists over at least 60 seconds** all miss it, and the reason says so ("not in its order list at 3 checks from … to …, 75 s after it was sent");
  - still unclear after every follow-up: it stays UNCONFIRMED and asks for a person. They check the broker's own order book, then press **Not placed?** in the order book, confirm, and the order is settled with their name;
  - placing is never retried blindly.
- **CANCEL**: a cancel is on its way, or is waiting for the broker's id to arrive.

An update that would move an order backwards, or that repeats one already applied, is **IGNORED** and still recorded in the audit trail. An order we never placed (one made in Kite's own app) appears as **EXTERNAL**.

## Journeys

Each journey below has a diagram; the sources are `context/images/v1-journey-*.mmd`.

### Partial fill

![Partial fill](../../context/images/v1-journey-partial-fill.png)

Each broker update carries the order's total filled quantity. Only the new part becomes a `Fill`, so the position grows by exactly what traded, once.

### Modify and cancel

![Modify and cancel](../../context/images/v1-journey-modify-cancel.png)

**A modify is refused, in words, when:**
- the broker hasn't acknowledged the order yet;
- a cancel is already on its way;
- nothing would change;
- the new quantity isn't above what has filled;
- it breaks the order rules or the gate.

**A cancel always passes the gate**, even while trading is stopped. A second click changes nothing. If the broker's id hasn't arrived yet, the cancel is remembered and sent as soon as it does.

### Rejects

![Rejects](../../context/images/v1-journey-reject.png)

Our rules, the risk gate and the throttle refuse an order **before it is sent**, saying why. A clear refusal from the broker also becomes REJECTED with the broker's words. An exchange reject arrives later, as an update. A call with **no clear answer** is the dangerous case: it stays PENDING and UNCONFIRMED, never assumed rejected, until the broker's order list settles it.

### Restart with recovery

![Restart](../../context/images/v1-journey-restart.png)

Until recovery says "ok", each account takes no new orders or modifies; cancels still pass. A problem keeps trading paused, named in words, until a person fixes it at the broker and restarts. Recovery is checked by `tests/test_recovery.py` and by the crash drill (`context/scripts/drill-restart.py`, 17/17). See also [incidents.md](../runbooks/incidents.md) § Trading paused after a restart.

### The broker disconnecting

![Disconnect](../../context/images/v1-journey-disconnect.png)

Working orders at the exchange don't depend on our connection. While the connection is down, the screens say the prices are old. Once it is back, prices and order states are brought up to date from the broker's own order book. See [incidents.md](../runbooks/incidents.md) § WebSocket down.

### STOP

![STOP](../../context/images/v1-journey-stop.png)

The switch is engaged **before** the cancels go out, so nothing new can slip in meanwhile. Positions are kept: STOP doesn't close them. Closing is a separate, deliberate action (square-off, MTM auto-close, or closing by hand). Only the account's owner, risk or an admin can resume.
