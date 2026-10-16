# End-of-day report and P&L history (K10.3, #215)

Reports are built from the **audit trail** (the source of truth): every fill, reject and alert is
there with its time.

```
audit trail ─► fills before the day ─► positions carried in, valued at the previous close
            ─► the day's fills, in order, once each ─► positions, realised P&L
            ─► the last prices ─► unrealised P&L
            ─► the day's executed orders ─► charges (docs/design/charges.md) ─► net
            ─► the day's rejects and alerts
16:00 IST, trading days: Reports.close_of_day ─► kept: <data>/reports/<account>/<day>.json
```

| Endpoint | What |
|---|---|
| `GET /api/accounts/{a}/reports` | today (India) and the days with a kept report |
| `GET /api/accounts/{a}/reports/{day}?format=json\|csv\|pdf` | the kept report; today: a live preview; an older day without one: rebuilt without prices |
| `GET /api/accounts/{a}/pnl?by=day\|instrument\|strategy&start=&end=` | P&L over a period, from kept reports |

## Rules (each a real failure)

- **The same convention as the live screen.** What was carried in starts at the previous close, so a day's P&L is that day's move. Adding up the days gives the P&L of the period: Monday's open ₹6,000 plus Tuesday's close ₹7,500 equals the ₹13,500 the trades made (tests, worked by hand).
- **Closing prices are kept.** Past prices aren't stored, so the report is built at 16:00 with that moment's prices and kept. A past day rebuilt later has no prices: carried positions are at cost and open positions show no unrealised P&L, and the report says so (`priced: false`, `notes`) instead of guessing.
- **Each fill once.** A fill read twice from the audit trail counts once (cumulative filled quantity, the position book's rule). The tradebook has a row per fill.
- **By strategy:** each fill belongs to its order's basket ("basket B1"), algo ("algo …"), or "manual"; what was carried in is "carried". Each strategy keeps its own book, so the strategies add up exactly to the account's gross P&L.
- **Alerts are named kinds** (position mismatch, failed recovery, lost or failed algo, kill switch, rejected GTT), not every note.
- **Missing days are listed**, never counted as zero.
- **Downloads match the screen.** CSV and PDF are made from the same report the API sends. The PDF comes from a small built-in writer: Courier text on A4 landscape pages, no extra dependency, and "Rs" in place of the rupee sign, which Courier lacks. Checked by reading it back with a PDF library and in a real browser download.
- **Files are safe.** One JSON file per account and day, written to a temporary file and then renamed. Account ids are checked before they become folder names.

## Screens

**Reports** (a tab beside Positions): the Day view and the History view.

**Day view**
- A day picker: today's preview first, then kept days, newest first. A preview re-reads itself after every fill.
- What kind of report it is: kept, preview, or rebuilt without prices.
- Totals: gross, charges, net, turnover and trade count.
- Positions (carried in, bought, sold, carried forward, last price, gross, charges, net), by strategy, trades, rejects and alerts.
- CSV and PDF downloads.

**History view**
- By day, instrument or strategy, over the last 30 days.
- Period totals.
- The missing days.

Checks:
- `tests/test_reports.py` (hand-worked days);
- `ui/src/widgets/reports/Reports.test.tsx`;
- `context/scripts/mutate-reports.py` (20/20);
- real browser with downloads: `context/scripts/e2e-reports.cjs`.
