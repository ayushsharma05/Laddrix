# Charges: P&L gross and net (K10.2, #214)

Every trade in India pays more than the price difference. Laddrix works out each charge from the
executed orders, publishes every line, and shows P&L both **gross** (before charges) and **net** (after).

```
today's executed orders (order manager) ─► ChargesService ─► ChargesModel.bill ─► ChargeBill
                                               │                 (charges/model.py)
     ticket: POST …/charges/estimate ──────────┘                 rates: ChargeSchedule (data)
screens ◄─ GET /api/accounts/{a}/charges, stream {"type": "charges", …} after each fill
        ◄─ GET /api/charges/schedule (every rate, with the date it was checked)
```

| Line | Charged on | Delivery (CNC) | Intraday (MIS) | Futures | Options (premium) |
|---|---|---|---|---|---|
| Brokerage | each executed order | 0 | 0.03%, at most ₹20 | 0.03%, at most ₹20 | ₹20 flat |
| STT | buy / sell value | 0.1% both | 0.025% sell | 0.05% sell | 0.15% sell |
| Exchange | turnover (NSE / BSE) | 0.00297% / 0.00375% | same | 0.00173% / 0 | 0.03503% / 0.0325% |
| SEBI fee | turnover | ₹10 a crore | | | |
| IPFT (NSE) | turnover | 0.0001% | 0.0001% | 0.0001% | 0.0005% |
| Stamp duty | buy value | 0.015% | 0.003% | 0.002% | 0.003% |
| GST | brokerage + exchange + SEBI + IPFT | 18% | | | |
| DP charges | each stock sold from the demat, per day | ₹15.34 | | | |

Rates checked on 15 Oct 2026 (`charges/schedules.py`, `KITE_STANDARD`). The STT on F&O
includes the Budget 2026 rise, in force from 1 April 2026. Sources disagree by a few rupees a
crore on the exchange and IPFT rates, so check them against the broker's charges page before
trading real money. A server can load another table from JSON (`LADDRIX_CHARGES`) without
a code change.

## Rules (each a real failure)

- **Brokerage per executed order, not per fill.** An order that fills in three parts pays ₹20 once.
- **F&O contracts trade on NFO/BFO, but NSE/BSE charge the fee.** `venue()` maps the codes. The hand-worked tests caught exchange charges and IPFT coming out as zero on every F&O trade before this.
- **Exact, then rounded once.** Amounts are exact fractions. Each line is rounded to the paisa per position, then added up, so a position's charges and the day's total always agree.
- **DP once a stock a day,** however many sell orders.
- **Today only.** Charges count orders created since midnight India time. An order with nothing executed pays nothing (no flat ₹20 for nothing).
- **Estimates need a quantity and a price above 0.** An unknown instrument is refused in words.

## Screens

- **Positions:** the bar shows Day gross · charges · net; the hover lists each line and the rates' date. Each row has Charges and Net P&L columns, and so does the total row.
- **Ticket:** "Charges ≈ ₹…" for the order as it stands (a server estimate, asked 250 ms after the last change), with each line in the hover.

## Not covered yet

- STT on options exercised at expiry (0.15% of the intrinsic value from 1 April 2026).
- Currency and commodity segments.
- Charges on orders placed outside Laddrix: those are in the broker's tradebook, not in our positions.

Checks: `tests/test_charges.py` (worked by hand per segment); `context/scripts/mutate-charges.py` (20/20); real browser `context/scripts/e2e-charges.cjs`.
