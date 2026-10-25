# Exact fills: what an order traded for, to the paisa

Issue #275. Code: `orders/manager.py` (`_filled_value`, `_fill_from`), `broker/kite/orders.py`
(`exact_paise`), `positions/book.py` (`apply`, `correct`).

## The problem it fixes

Kite tells us an order's **average price**, not each fill's price, and the average has
fractions of a paisa (₹25,010.0333…). Laddrix used to round it to whole paise and then
work out each new fill's price from the rounded averages:

```
 50 bought at ₹25,010.00, then 100 at ₹25,010.05
 average ₹25,010.0333… ─► rounded ₹25,010.03
 "new fill" = (150 x 25,010.03 − 50 x 25,010.00) / 100 = ₹25,010.045 ─► ₹25,010.04 (wrong)
 position cost ₹1 too low; every P&L after it off by ₹1
```

A broker correction (the same quantity, a new average) was thrown away as a "duplicate".

## The rule now

```
 broker update ─► exact average (Kite's text, fractions kept) x filled qty
                  = the order's exact traded value, in whole paise (exchange trades are whole
                    paise, so this is a whole number; rounded only if the broker rounded)
              ─► new fill = value now − value before; the fill keeps that exact value
                  (`Fill.value_paise`); its price is shown rounded
              ─► same qty, different value: a CORRECTED event with the difference;
                  positions move: the cost while the position is still held,
                  the realised P&L once it is closed
```

- **Paper** gives each fill's own price: those are exact already. A rounded average from it
  that agrees with what we know (within half a paisa) changes nothing.
- **Duplicates and late updates** change nothing: an update with no more filled and the same
  value is a duplicate; one with less filled is stale.
- **The audit trail** keeps each fill's exact value and each correction; after a restart the
  positions are rebuilt to the same paisa (`EventLog.fills` returns corrections too).
- **Shown values** (the order's average, a fill's price) are rounded only on screen; the
  maths uses the exact value.

## Tolerances

| Where | Exact? |
| --- | --- |
| Positions, realised and unrealised P&L | exact to the paisa, given the broker's average |
| A broker that rounds its average (not Kite) | within half a paisa per unit of the order |
| Charges | on the exact value; worked out again after a correction |
| Against the broker's trades | exact; a quantity that differs is named, not changed |

## Against the broker's trade list (#352)

`orders/trade_check.py` runs on each new trade list (and on each fill, against the last
list):

```
 trades ─► per broker order: the sum of its trades, each trade id once
           (a trade id read again counts once; one that comes back changed replaces the old)
 orders ─► same qty, different value ─► the trades win: a CORRECTED event
                                        (positions, charges follow)
           different qty, two reads in a row ─► TRADES_DIFFER in the audit trail, once;
                                                nothing changed by itself
```

Charges are worked out on the exact value, again after a correction. The daily report reads
fills and corrections from the audit trail, so it agrees with positions. Runbook:
[incidents.md](../runbooks/incidents.md#an-orders-fills-dont-match-the-brokers-trades-352).

## Trades made outside Laddrix (#355)

A trade made in the broker's own app moves the broker's position, not ours: reconciling
raises a mismatch. `positions/external.py` resolves it, never by setting our number to the
broker's:

```
 preview ─► the broker's trades in that position with no order of ours (each trade id once,
            minus any already adopted) ─► do they add up to the difference exactly?
 adopt   ─► risk or admin, with words on what they checked; the preview again, fresh;
            refused unless exact ─► each trade at its own price (true cost basis),
            audited (EXTERNAL_ADOPTED, with the trade id) ─► reconcile again: it clears
 restart ─► recovery applies today's adopted trades again from the audit trail
```

A difference the outside trades don't explain (a missed fill of ours, an old trade) stays an
alert for a person to resolve at the broker.
