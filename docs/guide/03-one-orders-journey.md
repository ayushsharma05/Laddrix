# Chapter 3 · One order's journey, through the code

[Guide home](README.md) · previous: [Chapter 2](02-big-picture.md) · next: [Chapter 4 · Prices](04-prices.md)

We follow one click, **BUY 75 NIFTY at ₹25,010**, from your mouse to "position +75", and look at
the real code at each stop.

---

## 3.1 The whole trip on one page

![The life of an order](../../context/images/v1-order-life.png)

The same trip in words, with the file at each stop:

```
 ① you click BUY on the ladder           ui/src/widgets/ladder/
 ② the screen sends a request            ui/src/api/rest.ts        POST /api/accounts/acc-paper/orders
 ③ the waiter checks who you are         src/laddrix/server/routes/trading.py   place()
 ④ the note becomes an OrderRequest      src/laddrix/domain/orders.py
 ⑤ the kitchen manager takes it          src/laddrix/orders/manager.py  OrderManager.submit()
 ⑥ the safety inspector checks it        src/laddrix/risk/checks.py (+ margin, throttle)
 ⑦ it goes to the broker                 src/laddrix/broker/paper.py  (or kite/)
 ⑧ the broker reports back               OrderManager.apply()
 ⑨ the news spreads                      positions, charges, ledger, algos, screens
 ⑩ your screen updates                   ui/src/api/stream.ts → ui/src/state/store.ts
```

## 3.2 Stop ④: what an order looks like inside

Money is **never** a decimal number inside Laddrix. It is a whole number of **paise**
(₹25,010.00 = 2501000 paise). Why? Computers store decimals like 0.1 slightly wrong, and with
money "slightly wrong" is not OK. Whole numbers are exact.

From `src/laddrix/domain/money.py`:

```python
PAISE_PER_RUPEE = 100


def to_paise(rupees: str | Decimal) -> int:
    """Turn a rupee amount like "2501.05" into whole paise (250105)."""
```

- `def to_paise(...)` makes a little machine called `to_paise`: rupees go in, paise come out.
- `-> int` means "what comes out is a whole number".
- The text in `"""…"""` is a note for people; Python ignores it.

An order is an `OrderRequest`, a frozen bundle of facts (`domain/orders.py`):

```python
class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PENDING"  # sent, broker hasn't confirmed yet
    OPEN = "OPEN"  # working at the exchange
    PARTIAL = "PARTIAL"  # partly filled, rest still working
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
```

- **`Enum`** means "one of a fixed list": a side is BUY or SELL, nothing else. That makes typos
  impossible.
- **`@dataclass(frozen=True)`** (above `OrderRequest`) means its facts can't be changed after it
  is made. A sent order should never quietly change.

## 3.3 Stop ⑤: the kitchen manager, `OrderManager.submit`

This is the most important function in the project (`orders/manager.py`). The real code,
shortened, with the meaning of each line:

```python
order = _Order(request=request, created_at=now, updated_at=now)
self._orders[request.client_order_id] = order  # remember it, by its id (MT7)
self._emit(EventKind.SUBMITTED, order)  # tell everyone: "a new order"

reason = self._rule_problem(request)  # basic sanity
if reason is None:
    reason = await self._gate.admit_new(request)  # ⑥ the safety inspector
if reason is not None:
    self._reject_locally(order, reason)  # refused: say why, stop here
    return order.view()

try:
    broker_order_id = await self._broker.place(request)  # ⑦ to the broker
except BrokerError as exc:
    self._reject_locally(order, exc.reason)  # the broker clearly said no
    return order.view()
except Exception as exc:  # NO clear answer (timeout…)
    self._mark_unconfirmed(order, f"no answer from broker: {exc!r}")
    self.follow_unconfirmed()  # keep asking the broker, by itself
    return order.view()

self._register_broker_id(order, broker_order_id)  # the broker's own id for it
self._emit(EventKind.SENT, order)  # tell everyone: "sent"
```

- **`await`** means "wait here for the answer, but let other work go on meanwhile". The server
  never freezes while one order waits.
- **`try / except`** is like saying "try this, and if it goes wrong in this way, do that instead".
- The three endings are refused, sent, and **unconfirmed** ("we don't know"). The third is
  the reason this code is careful: if we don't know whether an order reached the exchange,
  sending it again could make **two** orders. So Laddrix waits and asks instead.

## 3.4 Stop ⑥: the safety inspector (the gates)

```
 the order ──► risk  ──► margin ──► throttle ──► broker
               │          │           │
               │          │           └ "max 8 per second": waits for a free slot
               │          └ "do you have enough money for this?"
               └ a long list of questions (risk/checks.py, admit_new):
                  STOP on? lot size? tick size? price far from the market?
                  too big? too much value? daily loss reached? too many open orders?
                  the same order twice in 1 s (a double click)? closing more than you hold?
```

Each check returns either **nothing** ("fine") or a **sentence** saying what's wrong. The first
sentence stops the order, and that exact sentence is shown on your screen.
That's why refusals in Laddrix read like "75 is not a multiple of NIFTY26OCTFUT's lot size
75" and not "ERROR 4012".

## 3.5 Stops ⑧–⑨: the broker reports back, and the news spreads

The broker sends updates: OPEN, then PARTIAL with a fill, then FILLED. `OrderManager.apply`
reads each one and, if it really changes something, **announces** it.

Announcing works like a group chat: parts of Laddrix **subscribe** to the order manager, and
each announcement reaches all of them.

```
                    OrderManager announces "MT7 FILLED 75 @ ₹25,010"
                                      │
    ┌──────────────┬──────────────┬───┴────────┬──────────────┬──────────────┐
    ▼              ▼              ▼            ▼              ▼              ▼
 positions      charges        ledger       algos          loss limits    stream
 +75, avg       brokerage,     written to   "was that      check the      to every
 ₹25,010        taxes          events.db    my order?"     new P&L        open screen
```

This pattern ("subscribe, then announce") appears all over Laddrix. Look for
`.subscribe(` in `platform.py`: each line plugs one listener in.

## 3.6 Stop ⑩: your screen updates

![The order book, positions and audit trail](../../context/images/k5-5-screens.png)

```
 server stream ──► ui/src/api/stream.ts ──► ui/src/state/store.ts ──► every widget that shows orders
                    (keeps the line open,     (ONE box holding all       redraws itself
                     reconnects if cut)        orders, positions, prices)
```

Double-click any order in the order book to see its **audit trail**: every event from the
ledger, in order. It's the same trip you just read, but for a real order.

## 3.7 Other journeys (same idea, different endings)

Each picture shows one situation step by step.

**Part of the order fills, then the rest:**
![Partial fill](../../context/images/v1-journey-partial-fill.png)

**You change or cancel it while it waits:**
![Modify and cancel](../../context/images/v1-journey-modify-cancel.png)

**It is refused:**
![Reject](../../context/images/v1-journey-reject.png)

**The connection drops:**
![Disconnect](../../context/images/v1-journey-disconnect.png)

**The server restarts:**
![Restart](../../context/images/v1-journey-restart.png)

**You press STOP:**
![STOP](../../context/images/v1-journey-stop.png)

---

**Try it:** run the demo, place an order, double-click it in the order book, and match each line
of its audit trail with a stop in 3.1. Then go on to [Chapter 4](04-prices.md).
