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
