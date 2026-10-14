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
