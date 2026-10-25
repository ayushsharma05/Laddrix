# Funds, holdings and the tradebook (K10.1, #213)

The account's money and what it owns, read from the broker and shown as the broker reports it.

| What | Kite | Paper |
|---|---|---|
| Holdings: delivery shares from earlier days (settled + T1), average, last price, P&L, day change | `GET /portfolio/holdings` | CNC longs carried from earlier days (from the audit trail at each start) |
| Funds: available, used, opening, collateral, labelled lines, per segment | `GET /user/margins` | a cash model with plain margin rates (`broker/paper_funds.py`), marked **estimate** |
| Tradebook: every fill today | `GET /trades` | the paper broker's own fills |

## Replaceable parts

```
screens ─► GET /api/accounts/{a}/funds | holdings | trades [?fresh=true]
        ◄─ stream {"type": "portfolio", account_id, kind, items, as_of, error} when something changed
PortfolioService (portfolio/service.py) ─► optional broker ports (broker/base.py)
                                             FundsSource.funds()        Kite, Paper
                                             HoldingsSource.holdings()  Kite, Paper
                                             TradesSource.trades()      Kite, Paper
```

A broker without a port simply doesn't implement it: the API answers 404 "<broker> doesn't
report holdings" and the screen says so (the order book hides its Trades tab).

## Rules (each a real failure)

- **The broker's figures, untouched.** Kite's `net` is "available" and `debits` is "used". Laddrix
  doesn't add Kite's lines up again: the signs of Kite's M2M lines are Kite's business, and a
  total that disagrees with Kite's would mislead. Each line keeps Kite's meaning under a plain label.
- **A failed read never empties a screen.** The last good figures stay, with `as_of` (when the
  broker last answered) and `error` (why the latest refresh failed). The screen says
  "as of 10:31:05 · couldn't refresh: …". An older answer never replaces a newer one in the browser.
- **One read at a time per kind.** Many screens asking at once make one broker call.
- **Fresh soon after a fill.** A fill schedules one refresh of everything 0.5 s after the last fill
  of a burst; a timer refreshes every 30 s; Refresh on a screen asks the broker now.
- **Pushed only when something changed** (a new `as_of` alone isn't sent).
- **Holdings at live prices.** Holdings are re-marked from Laddrix's market data every 2 s
  (their instruments are subscribed); the broker's last price is only a snapshot.
- **Our order ids on the tradebook.** Kite's `/trades` doesn't carry our tag: each trade's broker
  order id is looked up in the order manager. Trades of orders placed elsewhere (Kite web, the
  phone app) show "outside Laddrix".
- **Delivery buys become holdings the next day**, as at Kite: today's CNC buys are positions;
  after a restart the paper broker hands earlier days' CNC longs over as holdings (recovery.py),
  never as positions. Holdings sold today show as `used_qty`.

## Paper money (an estimate)

```
opening   = starting balance (LADDRIX_PAPER_FUNDS, default ₹10 lakh)
            + P&L of earlier days (realised, and carried F&O settled at the close)
            − what the holdings cost
available = opening + today's realised + unrealised P&L − margin used + holdings sold today
```

Margin per instrument and product is the worst of the position as it is, and as it would be if
every working buy, or every working sell, filled. An order that only closes blocks nothing.

| | margin |
|---|---|
| CNC long | the full value ("Delivery margin") |
| CNC short (holdings sold today) | a credit, shown as "Holdings sold today" |
| MIS equity | 20% of the value |
| future | 12% of the contract value ("SPAN + exposure") |
| option bought | its premium ("Options premium") |
| option sold | 12% of the strike value ("SPAN + exposure") |

The rates are `PaperMargins` (replaceable). A long option's or delivery buy's "margin" is its
value, so together with its P&L the cash moves by exactly what was paid. Real SPAN needs the
exchange's files. K10.4 (#216) uses the broker's margin API for real accounts.

**Known limits:**
- Paper keeps only an "equity" segment (NSE/BSE cash and F&O; no MCX today).
- Paper holdings are rebuilt at each start (paper keeps no state across a restart, as with paper orders).
- Premium received on a sold option isn't added to cash. The option's P&L carries it instead.

## Screens

- **Funds** (a tab beside Positions): one section per segment: Available, Used, Opening
  (Collateral when not zero), then the broker's own lines under Available and Used; "estimate" on paper.
- **Holdings** (a tab beside Positions): instrument, qty (with T1 and sold today), average, last
  price, invested, value, P&L with %, day change, and a total row. A click drives the link group.
- **Order book → Trades**: today's fills, newest first, with our order id (double-click for its
  audit trail) or "outside Laddrix".

Checked in a real browser on the demo: `context/scripts/e2e-portfolio.cjs`.
Mutation check: `context/scripts/mutate-portfolio.py`.
