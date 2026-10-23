# Expiry day: futures and options that end

Every future and option has an **expiry date**. At the market close that day (15:30 India
time) it stops trading, and the exchange **settles** every open position in it. This page is
how MockTrade handles that (issue #290). Code: `src/mocktrade/refdata/expiry.py`.

## The rules (checked 23 Oct 2026, re-check before trading real money)

| Rule | What MockTrade does |
| --- | --- |
| Equity F&O contracts end at **15:30 India time** on their expiry date | `expires_at` = 15:30 on the expiry date; from that minute the contract is *expired* |
| **Index** F&O (NIFTY, BANKNIFTY, SENSEX…) settle **in cash** | `settlement: "cash"`: the position closes at the final settlement price |
| **Stock** F&O settle **by delivery** ("physical settlement"): an open future, or an option that ends in the money, becomes a trade in the shares | `settlement: "physical"`, said in the warning. *Paper settles these in cash too*: delivery of shares isn't simulated yet |
| An option that ends out of the money is worth nothing | settlement price 0 |
| Brokers add their own cut-offs (closing physically settled positions themselves, or refusing new ones, on or before expiry day) and raise margins on expiry day. These differ by broker and change | **Settings**, not guesses: `ExpiryRules(physical_open_cutoff=…, cash_open_cutoff=…)`. From the cut-off on the expiry day, only closing trades are allowed. Both are off by default |

How MockTrade tells index from stock F&O: if the contract's underlying is a listed share
(`NSE:<underlying>` or `BSE:<underlying>` is an equity), it settles by delivery; otherwise
(an index) in cash.

Sources (Zerodha support and Varsity, read 23 Oct 2026): stock F&O are compulsorily
physically settled, index F&O are cash settled, and margins rise on expiry day.
[Policy on physical settlement](https://support.zerodha.com/category/trading-and-markets/margin-leverage-and-product-and-order-types/articles/policy-on-physical-settlement) ·
[Quick note on physical settlement](https://zerodha.com/varsity/chapter/quick-note-on-physical-settlement/).
NSE's monthly index contracts expire on the month's last Tuesday (the demo's contracts follow
that; `marketdata/sim_universe.py`).

## The day, step by step

```
 the day before   ─► screens: nothing special (the expiry date is in the instrument)
                      server: expires_today false

 expiry day       ─► server: expires_today true, with one sentence ("Expires today at 15:30 on
 (before 15:30)       27 Oct 2026; settles in cash at the final settlement price")
                  ─► screens: a warning line on the ticket, the ladder and the option chain;
                      an "EXP" tag before the name in Positions and the watch list
                  ─► a broker cut-off (when set): from then, orders that open or add are
                      refused; closing trades go

 15:30            ─► expired: every order in it is refused, even a close (the exchange has
                      settled it); search leaves it out; the option chain drops that expiry;
                      the watch list drops it. Charts, recordings and reports still find it by
                      its id

 next trading     ─► 08:45 "new trading day": positions in it close at the final settlement
 day 08:45            price (in our book and at the paper broker), BEFORE the day rolls, so
                      the result belongs to expiry day and today starts at zero. Each one is
                      noted in the audit trail (EXPIRY_SETTLED)
```

**The final settlement price** (`Expiries.settlement_price`):

```
 a future  ─► its last price
 an option ─► the underlying's price: the same-expiry future (it ends at the index's price),
              else the share's last price
           ─► call: price − strike, put: strike − price, never below 0
           ─► no underlying price known: the option's own last price
```

Example: long 75 NIFTY 25000 CE bought at ₹120.05; NIFTY ends at 25,200. Settlement price
= 25,200 − 25,000 = ₹200. Realised P&L = (200 − 120.05) × 75 = ₹5,996.25.

## Restarts

| What happened | What a restart does |
| --- | --- |
| The server ran over the morning roll | The roll noted each settlement (EXPIRY_SETTLED); the restart applies the same price from the note |
| The server was off over expiry | Paper: the restart finds positions in contracts that ended before today and settles them (recovery's report says "N expired positions settled"). Live: the broker no longer lists them, so nothing is carried |

## Live trading

The broker settles expired positions itself; MockTrade's book settles the same way at the
next morning's roll, so reconciling finds both sides flat. The settlement price MockTrade
uses is its own estimate (the last prices it saw); the broker's contract note and P&L are
the official numbers. Stock F&O delivered as shares appear as holdings at the broker.

## Not yet

- Paper delivery of shares for stock F&O (paper settles them in cash and says so).
- Expiry-day margin increases in the paper margin model.
- An exchange holiday that moves an expiry earlier: contracts carry the real date from the
  broker's instrument list, so live is right; the demo's dates ignore holidays.
