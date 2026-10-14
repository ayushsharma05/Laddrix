"""Option strategies: templates, legs at live prices, and what the position does.

    templates()                       the recipes the screen offers (backend provides)
    build(code, underlying, expiry,   -> legs: real contracts around the ATM at their
          width, lots)                   live prices
    analyse(legs)                     -> payoff at expiry and today over a price range,
                                         breakevens, max profit / loss (or "unlimited"),
                                         net premium and net Greeks

All money in rupees for the whole position (lots x lot size). "At expiry" means the nearest
leg expiry: legs that expire later are valued then by Black-76 at their current IV, so a
calendar spread's payoff is a curve, not a hockey stick.
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from mocktrade.domain.instruments import Instrument, InstrumentKind
from mocktrade.options.chain import ChainService
from mocktrade.options.model import (
    CLOSE,
    IST,
    YEAR_SECONDS,
    option_quote,
    time_to_expiry,
)
from mocktrade.options.pricing import black76, greeks

K = InstrumentKind
CE, PE = K.CALL, K.PUT


class StrategyError(ValueError):
    """A leg or request that can't be built or analysed; the message says which."""


# --- templates: legs relative to the ATM strike, in strike steps of `width` ---------------------


@dataclass(frozen=True)
class LegRecipe:
    side: str  # "BUY" / "SELL"
    kind: InstrumentKind
    offset: int  # in units of `width` strike steps from ATM (negative = below)
    ratio: int = 1  # lots per strategy lot


@dataclass(frozen=True)
class Template:
    code: str
    name: str
    view: str  # what it bets on, in one line
    legs: tuple[LegRecipe, ...]


B, S = "BUY", "SELL"
TEMPLATES: tuple[Template, ...] = (
    Template(
        "LONG_STRADDLE",
        "Long straddle",
        "a big move either way",
        (LegRecipe(B, CE, 0), LegRecipe(B, PE, 0)),
    ),
    Template(
        "SHORT_STRADDLE",
        "Short straddle",
        "a quiet market",
        (LegRecipe(S, CE, 0), LegRecipe(S, PE, 0)),
    ),
    Template(
        "LONG_STRANGLE",
        "Long strangle",
        "a big move, cheaper than a straddle",
        (LegRecipe(B, CE, 1), LegRecipe(B, PE, -1)),
    ),
    Template(
        "SHORT_STRANGLE",
        "Short strangle",
        "a range-bound market",
        (LegRecipe(S, CE, 1), LegRecipe(S, PE, -1)),
    ),
    Template(
        "BULL_CALL_SPREAD",
        "Bull call spread",
        "a moderate rise, limited risk",
        (LegRecipe(B, CE, 0), LegRecipe(S, CE, 1)),
    ),
    Template(
        "BEAR_PUT_SPREAD",
        "Bear put spread",
        "a moderate fall, limited risk",
        (LegRecipe(B, PE, 0), LegRecipe(S, PE, -1)),
    ),
    Template(
        "BULL_PUT_SPREAD",
        "Bull put spread",
        "not falling; collects premium",
        (LegRecipe(S, PE, 0), LegRecipe(B, PE, -1)),
    ),
    Template(
        "BEAR_CALL_SPREAD",
        "Bear call spread",
        "not rising; collects premium",
        (LegRecipe(S, CE, 0), LegRecipe(B, CE, 1)),
    ),
    Template(
        "IRON_CONDOR",
        "Iron condor",
        "a range, with both sides' risk capped",
        (LegRecipe(B, PE, -2), LegRecipe(S, PE, -1), LegRecipe(S, CE, 1), LegRecipe(B, CE, 2)),
    ),
    Template(
        "IRON_BUTTERFLY",
        "Iron butterfly",
        "staying near the ATM, risk capped",
        (LegRecipe(B, PE, -1), LegRecipe(S, PE, 0), LegRecipe(S, CE, 0), LegRecipe(B, CE, 1)),
    ),
    Template(
        "CALL_BUTTERFLY",
        "Call butterfly",
        "ending at the ATM, cheap and capped",
        (LegRecipe(B, CE, -1), LegRecipe(S, CE, 0, 2), LegRecipe(B, CE, 1)),
    ),
    Template(
        "CALL_RATIO_SPREAD",
        "Call ratio spread (1x2)",
        "a modest rise; unlimited risk above",
        (LegRecipe(B, CE, 0), LegRecipe(S, CE, 1, 2)),
    ),
)


def templates() -> list[dict[str, Any]]:
    return [
        {
            "code": t.code,
            "name": t.name,
            "view": t.view,
            "legs": [
                {"side": r.side, "kind": r.kind.value, "offset": r.offset, "ratio": r.ratio}
                for r in t.legs
            ],
            "params": [
                {
                    "name": "width",
                    "label": "Width (strikes)",
                    "kind": "count",
                    "min": 1,
                    "max": 20,
                    "default": 2,
                },
                {
                    "name": "lots",
                    "label": "Lots",
                    "kind": "lots",
                    "min": 1,
                    "max": 100,
                    "default": 1,
                },
            ],
        }
        for t in TEMPLATES
    ]


# --- legs ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    instrument: Instrument
    side: str  # BUY / SELL
    lots: int
    price_paise: float  # entry price per unit (live mid if not given)
    iv: float | None  # current implied volatility

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    @property
    def units(self) -> int:
        return self.sign * self.lots * self.instrument.lot_size


class Strategies:
    def __init__(self, chains: ChainService, clock=lambda: datetime.now(UTC)) -> None:
        self._chains = chains
        self._clock = clock

    def build(self, code: str, underlying: str, expiry: date, width: int, lots: int) -> list[dict]:
        template = next((t for t in TEMPLATES if t.code == code), None)
        if template is None:
            raise StrategyError(f"no strategy called {code!r}")
        if not 1 <= width <= 20 or not 1 <= lots <= 100:
            raise StrategyError("width must be 1-20 strikes and lots 1-100")
        series = self._chains._series(underlying, expiry)
        forward = self._chains.forward(underlying, expiry)
        if not series.strikes or forward is None:
            raise StrategyError(f"no priced options on {underlying} {expiry}")
        atm_index = series.strikes.index(min(series.strikes, key=lambda k: abs(k - forward)))
        legs = []
        for recipe in template.legs:
            index = atm_index + recipe.offset * width
            if not 0 <= index < len(series.strikes):
                raise StrategyError(f"{template.name} with width {width} needs strikes not listed")
            side = series.calls if recipe.kind is CE else series.puts
            option = side.get(series.strikes[index])
            if option is None:
                raise StrategyError(f"no {recipe.kind.value} at {series.strikes[index] / 100:g}")
            legs.append(
                {"instrument_id": option.id, "side": recipe.side, "lots": lots * recipe.ratio}
            )
        return legs

    def legs(self, raw: list[dict]) -> list[Leg]:
        """Requested legs, resolved to contracts with their entry price and current IV."""
        if not 1 <= len(raw) <= 8:
            raise StrategyError("a strategy has 1 to 8 legs")
        out = []
        now = self._clock()
        for item in raw:
            instrument = self._chains._store.get(str(item.get("instrument_id")))
            if instrument is None or instrument.kind not in (CE, PE):
                raise StrategyError(f"{item.get('instrument_id')} is not a listed option")
            side = item.get("side")
            lots = item.get("lots")
            if side not in ("BUY", "SELL") or not isinstance(lots, int) or not 1 <= lots <= 1000:
                raise StrategyError(
                    f"{instrument.symbol}: side must be BUY or SELL and lots 1-1000"
                )
            live = self._chains._price(instrument.id)
            price = item.get("price_paise") or live
            if price is None:
                raise StrategyError(f"{instrument.symbol} has no price yet")
            forward = self._chains.forward(instrument.underlying or "", instrument.expiry)
            quote = option_quote(instrument, live or price, forward, now)
            out.append(Leg(instrument, side, lots, float(price), quote.iv))
        underlyings = {leg.instrument.underlying for leg in out}
        if len(underlyings) != 1:
            raise StrategyError("all legs must be on the same underlying")
        return out

    def analyse(self, raw: list[dict], points: int = 121) -> dict[str, Any]:
        legs = self.legs(raw)
        now = self._clock()
        underlying = legs[0].instrument.underlying or ""
        horizon = min(leg.instrument.expiry for leg in legs)
        forward = self._chains.forward(underlying, horizon)
        if forward is None:
            raise StrategyError(f"no price for {underlying}'s future")
        F = forward / 100
        ivs = [leg.iv for leg in legs if leg.iv]
        typical_iv = sum(ivs) / len(ivs) if ivs else 0.15
        T_h = max(time_to_expiry(horizon, now), 1 / 365)
        # the useful view: about +-2 standard deviations (at least +-2%), always taking in
        # every strike with some room either side
        spread = max(2 * typical_iv * math.sqrt(T_h), 0.02)
        strikes = [leg.instrument.strike_paise / 100 for leg in legs]
        room = 0.25 * (max(strikes) - min(strikes)) + F * 0.005
        lo = min(F * math.exp(-spread), min(strikes) - room)
        hi = max(F * math.exp(spread), max(strikes) + room)
        xs = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
        horizon_at = datetime.combine(horizon, CLOSE, tzinfo=IST)
        rate = self._chains._rate

        def value_at(leg: Leg, s: float, when: datetime) -> float:
            """One unit of the leg, in rupees, if the future is at s at `when`."""
            k = leg.instrument.strike_paise / 100
            call = leg.instrument.kind is CE
            T = (
                max(
                    (
                        datetime.combine(leg.instrument.expiry, CLOSE, tzinfo=IST) - when
                    ).total_seconds(),
                    0,
                )
                / YEAR_SECONDS
            )
            return black76(s, k, T, rate, leg.iv or typical_iv, call)

        def pnl(s: float, when: datetime) -> float:
            return sum(leg.units * (value_at(leg, s, when) - leg.price_paise / 100) for leg in legs)

        at_expiry = [pnl(x, horizon_at) for x in xs]
        today = [pnl(x, now) for x in xs]
        premium = -sum(leg.units * leg.price_paise / 100 for leg in legs)  # + = credit
        net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        T_now = {leg: time_to_expiry(leg.instrument.expiry, now) for leg in legs}
        for leg in legs:
            g = greeks(
                F,
                leg.instrument.strike_paise / 100,
                T_now[leg],
                rate,
                leg.iv or typical_iv,
                leg.instrument.kind is CE,
            )
            for name in net:
                net[name] += leg.units * getattr(g, name)
        same_expiry = all(leg.instrument.expiry == horizon for leg in legs)
        extremes = _expiry_extremes(legs) if same_expiry else None
        return {
            "underlying": underlying,
            "expiry": horizon.isoformat(),
            "forward_paise": round(forward),
            "prices": [round(x, 2) for x in xs],
            "payoff_expiry": [round(v, 2) for v in at_expiry],
            "payoff_today": [round(v, 2) for v in today],
            "breakevens": _breakevens(legs) if same_expiry else _crossings(xs, at_expiry),
            "max_profit": extremes["max_profit"] if extremes else round(max(at_expiry), 2),
            "max_loss": extremes["max_loss"] if extremes else round(min(at_expiry), 2),
            "net_premium": round(premium, 2),
            "net_greeks": {k: round(v, 4) for k, v in net.items()},
            "legs": [
                {
                    "instrument_id": leg.instrument.id,
                    "side": leg.side,
                    "lots": leg.lots,
                    "lot_size": leg.instrument.lot_size,
                    "strike_paise": leg.instrument.strike_paise,
                    "kind": leg.instrument.kind.value,
                    "expiry": leg.instrument.expiry.isoformat(),
                    "price_paise": round(leg.price_paise, 2),
                    "iv": round(leg.iv, 6) if leg.iv else None,
                }
                for leg in legs
            ],
        }


# --- exact expiry maths (all legs on one expiry: the payoff is piecewise linear) --------------


def _expiry_value(legs: list[Leg], s: float) -> float:
    total = 0.0
    for leg in legs:
        k = leg.instrument.strike_paise / 100
        intrinsic = max(s - k, 0.0) if leg.instrument.kind is CE else max(k - s, 0.0)
        total += leg.units * (intrinsic - leg.price_paise / 100)
    return total


def _kinks(legs: list[Leg]) -> list[float]:
    return sorted({leg.instrument.strike_paise / 100 for leg in legs})


def _slope_above(legs: list[Leg]) -> float:
    """dP/dS once the future is above every strike: only calls move, one rupee per unit."""
    return float(sum(leg.units for leg in legs if leg.instrument.kind is CE))


def _expiry_extremes(legs: list[Leg]) -> dict[str, float | str]:
    points = [0.0, *_kinks(legs)]
    values = [_expiry_value(legs, p) for p in points]
    slope = _slope_above(legs)
    best, worst = max(values), min(values)
    return {
        "max_profit": "unlimited" if slope > 0 else round(best, 2),
        "max_loss": "unlimited" if slope < 0 else round(worst, 2),
    }


def _breakevens(legs: list[Leg]) -> list[float]:
    """Exact zero crossings of the expiry payoff (linear between strikes)."""
    kinks = _kinks(legs)
    points = [0.0, *kinks]
    values = [_expiry_value(legs, p) for p in points]
    found = _crossings(points, values)
    slope = _slope_above(legs)
    last = values[-1]
    if slope and (last < 0 < slope or last > 0 > slope):
        found.append(round(kinks[-1] - last / slope, 2))
    return sorted(set(found))


def _crossings(xs: list[float], ys: list[float]) -> list[float]:
    out = []
    for (x0, y0), (x1, y1) in zip(
        zip(xs, ys, strict=False), zip(xs[1:], ys[1:], strict=False), strict=False
    ):
        if y0 == 0 and x0 > 0:
            out.append(round(x0, 2))
        elif (y0 < 0 < y1) or (y0 > 0 > y1):
            out.append(round(x0 + (x1 - x0) * (-y0) / (y1 - y0), 2))
    return out
