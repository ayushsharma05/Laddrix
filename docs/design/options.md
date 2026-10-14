# Options: pricing, chain, strategies

## Pricing (`src/mocktrade/options/`, #189)

- **Black-76 on the future.** NSE and BSE index and stock options are European. The future's
  price already includes interest and expected dividends, so pricing off it needs no guess
  about either.
- **Which future:** the same-expiry future when one is listed. Otherwise (weekly index options)
  the nearest listed future, carried to the option's expiry at the configured rate (default
  6.5%, a setting rather than a fact).
- **Time:** calendar years to 15:30 India time on the expiry day. An option is worth its
  intrinsic value from then on.
- **Price used:** the mid of a two-sided market, else the last trade. With no price, the
  screens show "–", never a made-up 0.
- **Implied volatility:** Newton steps kept inside a bisection bracket. It is fast near the
  money and never runs away deep in or out of the money. The result is "no IV" when no
  volatility explains the price (at or below intrinsic, at the no-arbitrage ceiling, expired).
- **Greeks, in traders' units:**

  | Greek | Unit |
  |---|---|
  | delta | per ₹1 of the future |
  | gamma | per ₹1 |
  | theta | ₹ per calendar day |
  | vega | ₹ per 1 point of IV |

- **Tests:** a textbook value (Hull), put–call parity, Greeks against finite differences, and
  IV round trips from 10 minutes to 1 year, from deep ITM to deep OTM. A mutation check
  catches 16 of 16 formula changes.

## Option chain (`options/chain.py`, #190)

| API | |
|---|---|
| `GET /api/options` | underlyings with listed options |
| `GET /api/options/{underlying}/expiries` | expiry dates, nearest first (expired ones never) |
| `GET /api/options/{underlying}/{expiry}/chain?strikes=15` | a snapshot: ATM ± 15 strikes |
| stream `{"op":"chain","underlying","expiry","strikes"}` → `{"type":"chain",…}` | at once, then at most once a second while anything in it changes; `chain_stop` ends it; 2 per screen |

- **Each row (call and put):** LTP, bid/ask, volume, OI, change in OI, IV, delta, gamma, theta,
  vega.
- **The chain:** the forward, ATM strike, days to expiry, rate, PCR (by OI of the shown strikes),
  max pain and ATM IV.
- **Change in OI** is measured from the first OI seen today. Kite's feed has no previous-day
  OI, and the screen says so.
- **Streaming:** a chain subscribes only ATM ± N strikes plus the futures, through MarketData's
  demand hook, and follows the ATM as the market moves. A NIFTY chain never uses up the
  feed's 3000-token budget.
- **The simulator** prices options from their future with Black-76 and a volatility smile,
  plus open interest. So the demo's chain behaves like a real one: about 12% IV at the
  money, a put skew, and put–call parity holding.

## Strategy builder (`options/strategy.py`, #192)

| API | |
|---|---|
| `GET /api/options/strategies` | the templates (legs relative to the ATM) and their parameters |
| `POST /api/options/strategy/build` `{code, underlying, expiry, width, lots}` | real contracts around the ATM, analysed |
| `POST /api/options/strategy/analyse` `{legs: [{instrument_id, side, lots, price_paise?}]}` | the analysis |

- **Templates:** long and short straddle, long and short strangle, bull call, bear put, bull put
  and bear call spreads, iron condor, iron butterfly, call butterfly, and the 1×2 call ratio.
  A new template is a server change only.
- **Analysis (whole position, ₹):**
  - payoff at expiry and today over ±2σ (always taking in every strike);
  - exact breakevens (the expiry payoff is linear between strikes);
  - max profit and max loss, "unlimited" when the slope above the top strike says so;
  - net premium (credit or debit) and net Greeks.
- **Calendars:** valued at the nearest expiry, with the later legs priced by Black-76 at their
  current IV, so the payoff is a curve rather than a hockey stick.
- **Screen:** template, width and lots, then Build. Legs can be flipped, resized or removed.
  Alt-click in the option chain adds a buy leg (Alt+Shift a sell), and opens a builder if
  none is open. The chart is the server's curves, scaled. The analysis refreshes every 3 s
  while open.
- **Tests:** iron condor, straddle, ratio spread and butterfly checked by hand; the today curve
  meets the expiry payoff at the close; Greeks add up; calendars. A mutation check catches
  13 of 13 changes.

## Baskets (`orders/basket.py`, #193)

| API | |
|---|---|
| `POST /api/accounts/{a}/baskets/check` `{legs: [{instrument_id, side, lots, product?, price_paise?, at?}]}` | each leg's risk result, and the margin |
| `POST /api/accounts/{a}/baskets/send` | 201 with the orders sent; 422 "nothing sent" if any leg fails the check |

- **One check for all legs.** Every leg goes through the normal risk checks, and each leg sees
  the legs before it as already working. If any leg fails, nothing is sent and the result
  names the leg.
- **Covered shorts.** By default an account can't open a naked short option. A short covered
  by longs of the same underlying, type and expiry (held, working, or earlier legs of the
  basket) has defined risk, so it is allowed. Spreads and condors work; naked shorts still
  need `allow_option_selling`.
- **Send order:** option buys first, so the protection is in place before the shorts. A leg
  that is refused or unconfirmed stops the rest, and the result says which legs are working.
- **Prices:** a leg's own limit, or "touch" (buy at the offer, sell at the bid) or "mid"
  (rounded to the tick on the side that doesn't overpay). Every leg is a LIMIT order.
- **Margin:** brokers that can quote it implement `MarginQuoter` (Kite: `POST /margins/basket`,
  initial and final, giving the hedge benefit). The paper broker says it can't, rather than
  guessing.
- Each leg carries the basket id, which is stored with the order and shown in the order book,
  and the audit trail records `BASKET_SENT` or `BASKET_STOPPED`.
- **Screen:** the strategy builder's Trade panel (product, touch/mid, Check, Send N orders).
  Changing anything requires a new check before sending.
