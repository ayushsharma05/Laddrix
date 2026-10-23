# Compliance: surveillance alerts, the auditor's export, the exchange algo id

Issue #291. Code: `risk/surveillance.py`; endpoints in `server/app.py`.

## The rules this is built on (checked 23 Oct 2026, re-check before going live)

**SEBI's retail algo framework** (circular of February 2025, in force from 1 April 2026):
- Orders from an API or algo above **10 orders a second, per exchange**, need the strategy
  registered with the exchange through the broker. Each registered strategy gets an
  exchange **algo id** that its orders carry.
- Below that, a trader running their own algo for their own account needs no registration.
- Brokers watch order rates and refuse orders that break the limits. Orders from an API need
  a static IP that the broker has on file. Kite allows 10 orders a second and answers 429
  above that.

These come from broker and industry summaries, not the circular itself. Read the SEBI
circular and the NSE/BSE notices before trading real money.
- [HDFC Sky: SEBI algo trading rules 2026](https://hdfcsky.com/sky-learn/algo-trading/sebi-algo-trading-rules)
- [Tradejini: SEBI's new algo rules](https://www.tradejini.com/blogs/what-sebis-new-algo-trading-rules-mean-for-you)
- [Kite Connect forum: preparing for SEBI's retail algo rules](https://kite.trade/forum/discussion/15912/preparing-to-comply-with-sebis-retail-algo-rules-static-ip-ratelimits-order-types)
- [Kite Connect API FAQs](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs)

**MockTrade stays below the line by design:** its throttle allows 8 orders a second
(`risk/throttle.py`), so its algos need no algo id. Running faster, or offering algos to
other people, means registering first (the algo id, below).

## Surveillance alerts (for the risk role)

```
 every order event (all accounts) ─► possible self-trade: a new order that could match a
                                      working order on the other side, of the same account
                                      or the same user (prices cross, or either is MARKET)
                                  ─► unusually large: more than 5x the account's median
                                      order size today, once it has 10 orders
                                  ─► high cancel/modify ratio: cancels + changes at least 3x
                                      the orders placed, once it has 10 orders
                                  ─► a burst: 8 orders within one second (the line is 10)
```

- **Never blocking:** an alert never stops an order. The risk checks do that; an alert
  asks a person to look.
- **Kept:** each alert goes into the audit trail (SURVEILLANCE) and is shown to risk and
  admin.
- **Acknowledged:** someone in risk acknowledges it with a note (SURVEILLANCE_ACK). Both
  come back after a restart.
- **Not repeated:** each kind is raised at most once a day per account and instrument; a
  burst at most once a minute.
- **Thresholds:** they are settings (`SurveillanceLimits`).

| Endpoint | Who | What |
| --- | --- | --- |
| `GET /api/surveillance[?open_only=true]` | risk, admin | the alerts |
| `POST /api/surveillance/{id}/ack` `{"note"}` | risk, admin | acknowledge, with what was found |
| `GET /api/accounts/{id}/audit-export?day=YYYY-MM-DD` | risk, admin | the account's whole audit trail for the day as CSV: every order event, fill, note and alert, in order |

## The exchange algo id

See the section below once #291's second part lands.
