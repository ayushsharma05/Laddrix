"""Black-76: European options priced off the future, with implied volatility and Greeks.

NSE and BSE index and stock options are European. Pricing off the future (Black-76) instead
of the spot (Black-Scholes) needs no guess about dividends or carry: the future's price
already holds them. Units here are plain floats: prices in rupees, T in years, r and sigma
as fractions (0.065, 0.14). The screens' units (per-day theta, vega per 1 point of IV) are
in `Greeks`.

    d1 = (ln(F/K) + sigma^2 T / 2) / (sigma sqrt T)      d2 = d1 - sigma sqrt T
    call = e^-rT (F N(d1) - K N(d2))                     put = e^-rT (K N(-d2) - F N(-d1))
"""

import math
from dataclasses import dataclass

SQRT_2PI = math.sqrt(2 * math.pi)
MIN_VOL, MAX_VOL = 1e-4, 5.0  # 0.01% to 500%: outside this, "no IV" rather than a number


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1_d2(F: float, K: float, T: float, sigma: float) -> tuple[float, float]:
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    return d1, d1 - v


def black76(F: float, K: float, T: float, r: float, sigma: float, call: bool) -> float:
    """The option's fair value. At or after expiry (T <= 0): its intrinsic value."""
    if F <= 0 or K <= 0:
        raise ValueError("prices must be positive")
    if T <= 0 or sigma <= 0:
        return max(F - K, 0.0) if call else max(K - F, 0.0)
    d1, d2 = _d1_d2(F, K, T, sigma)
    df = math.exp(-r * T)
    if call:
        return df * (F * norm_cdf(d1) - K * norm_cdf(d2))
    return df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


@dataclass(frozen=True)
class Greeks:
    """As traders read them: delta per 1 rupee of the future, gamma per rupee, theta in
    rupees per calendar day, vega in rupees per 1 percentage point of volatility."""

    delta: float
    gamma: float
    theta: float
    vega: float


def greeks(F: float, K: float, T: float, r: float, sigma: float, call: bool) -> Greeks:
    if T <= 0 or sigma <= 0:
        itm = (F > K) if call else (F < K)
        return Greeks((1.0 if call else -1.0) if itm else 0.0, 0.0, 0.0, 0.0)
    d1, _ = _d1_d2(F, K, T, sigma)
    df = math.exp(-r * T)
    sqrt_t = math.sqrt(T)
    pdf = norm_pdf(d1)
    delta = df * norm_cdf(d1) if call else -df * norm_cdf(-d1)
    gamma = df * pdf / (F * sigma * sqrt_t)
    vega_per_unit = df * F * pdf * sqrt_t  # per 1.00 (100 points) of volatility
    price = black76(F, K, T, r, sigma, call)
    theta_per_year = -df * F * pdf * sigma / (2 * sqrt_t) + r * price
    return Greeks(delta, gamma, theta_per_year / 365.0, vega_per_unit / 100.0)


def implied_vol(
    price: float, F: float, K: float, T: float, r: float, call: bool, tol: float = 1e-8
) -> float | None:
    """The volatility at which Black-76 gives `price`, or None when no volatility does
    (price at or below intrinsic, at or above the no-arbitrage ceiling, expired).

    Newton steps kept inside a bisection bracket: fast near the answer, never runs away
    (deep in or out of the money, where vega is tiny)."""
    if T <= 0 or price <= 0 or F <= 0 or K <= 0:
        return None
    df = math.exp(-r * T)
    intrinsic = df * (max(F - K, 0.0) if call else max(K - F, 0.0))
    ceiling = df * (F if call else K)
    if price <= intrinsic + 1e-12 or price >= ceiling:
        return None
    lo, hi = MIN_VOL, MAX_VOL
    if black76(F, K, T, r, hi, call) < price or black76(F, K, T, r, lo, call) > price:
        return None
    # Brenner-Subrahmanyam: a good first guess near the money
    sigma = min(max(math.sqrt(2 * math.pi / T) * price / (df * F), lo), hi)
    for _ in range(100):
        value = black76(F, K, T, r, sigma, call)
        diff = value - price
        if abs(diff) < tol:
            return sigma
        if diff > 0:
            hi = sigma
        else:
            lo = sigma
        vega = df * F * norm_pdf(_d1_d2(F, K, T, sigma)[0]) * math.sqrt(T)
        step = sigma - diff / vega if vega > 1e-12 else None
        sigma = step if step is not None and lo < step < hi else (lo + hi) / 2
        if hi - lo < 1e-12:
            return sigma
    return sigma
