"""From instruments and live prices to the numbers pricing needs.

time_to_expiry   years from now to 15:30 India time on the expiry day (calendar time)
forward_for      the future price to price an option off: the same-expiry future when
                 listed, else the nearest listed future carried to the option's expiry
                 (weekly index options have no future of their own)
option_quote     price, IV and Greeks for one option, or None parts when there is no
                 price to work from (the screens show "–", never a made-up 0)
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from laddrix.domain.instruments import Instrument, InstrumentKind
from laddrix.domain.ticks import Tick
from laddrix.options.pricing import Greeks, black76, greeks, implied_vol

IST = ZoneInfo("Asia/Kolkata")
CLOSE = time(15, 30)
YEAR_SECONDS = 365.0 * 24 * 3600
DEFAULT_RATE = 0.065  # annual, continuous; a setting (RBI repo / T-bill level), not a fact


def time_to_expiry(expiry: date, now: datetime) -> float:
    """Years until 15:30 India time on the expiry day; 0 once it has passed."""
    end = datetime.combine(expiry, CLOSE, tzinfo=IST)
    return max((end - now).total_seconds(), 0.0) / YEAR_SECONDS


def mid_or_last(tick: Tick | None) -> float | None:
    """The price to read an option or future at, in paise: the mid of a two-sided market,
    else the last trade."""
    if tick is None:
        return None
    bid, ask = tick.best_bid, tick.best_ask
    if bid is not None and ask is not None and ask.price_paise >= bid.price_paise:
        return (bid.price_paise + ask.price_paise) / 2
    return float(tick.ltp_paise) if tick.ltp_paise > 0 else None


def forward_for(
    option: Instrument,
    futures: list[Instrument],
    price_of,  # instrument id -> paise or None
    now: datetime,
    rate: float = DEFAULT_RATE,
) -> float | None:
    """The forward (paise) for the option's expiry, from the listed futures of its underlying."""
    expiry = option.expiry
    same = [(f, f.expiry) for f in futures
            if f.underlying == option.underlying and f.expiry is not None]  # fmt: skip
    if not same or expiry is None:
        return None
    # the same-expiry future if listed (0 days apart), else the nearest one
    chosen, chosen_expiry = min(same, key=lambda pair: abs((pair[1] - expiry).days))
    price = price_of(chosen.id)
    if price is None:
        return None
    if chosen_expiry == expiry:
        return price
    gap = time_to_expiry(expiry, now) - time_to_expiry(chosen_expiry, now)
    return price * math.exp(rate * gap)


@dataclass(frozen=True)
class OptionQuote:
    price_paise: float | None
    iv: float | None  # a fraction (0.14 = 14%)
    greeks: Greeks | None
    years: float


def option_quote(
    option: Instrument,
    option_price_paise: float | None,
    forward_paise: float | None,
    now: datetime,
    rate: float = DEFAULT_RATE,
) -> OptionQuote:
    if option.kind not in (InstrumentKind.CALL, InstrumentKind.PUT) or option.expiry is None:
        raise ValueError(f"{option.id} is not an option")
    years = time_to_expiry(option.expiry, now)
    call = option.kind is InstrumentKind.CALL
    if option_price_paise is None or forward_paise is None or option.strike_paise is None:
        return OptionQuote(option_price_paise, None, None, years)
    F, K, price = forward_paise / 100, option.strike_paise / 100, option_price_paise / 100
    iv = implied_vol(price, F, K, years, rate, call)
    g = greeks(F, K, years, rate, iv, call) if iv is not None else None
    return OptionQuote(option_price_paise, iv, g, years)


def fair_value_paise(
    option: Instrument,
    forward_paise: float,
    sigma: float,
    now: datetime,
    rate: float = DEFAULT_RATE,
) -> float:
    if option.expiry is None or option.strike_paise is None:
        raise ValueError(f"{option.id} is not an option")
    years = time_to_expiry(option.expiry, now)
    call = option.kind is InstrumentKind.CALL
    return 100 * black76(forward_paise / 100, option.strike_paise / 100, years, rate, sigma, call)
