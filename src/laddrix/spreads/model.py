"""A multi-leg spread and its live implied price (#414, part of #250).

A spread is 2 to 6 legs traded as one thing. Each leg has:

    ratio     lots of this leg per spread lot; its sign says the side: buying the spread
              buys the + legs and sells the − legs
    price ×   how much its price counts in the spread's price (the same sign as the ratio;
              usually the ratio itself, e.g. a 1:−2:1 butterfly prices as A − 2B + C)
    quote     a leg that rests a quote; one or more (#427), the others are only hedged
    pay-up    how many ticks worse than the best price its hedge may pay

And the spread has its trading controls (#425), used by the Autospreader algo:

    re-quote threshold   ticks the quote's target must move before the quote is moved
                         (a move that would make the spread worse than its level is at once)
    max unhedged         the most spread lots one quote may carry (0: no limit), so one fill
                         never leaves more than this unhedged
    chase                ticks a working hedge may follow the market past its first price
    hedge timeout        seconds a leg may stay unhedged before an ALERT

The spread's price is   Σ price× · leg price.   What it costs to trade it at once:

    ask (buy the spread now)  = Σ price× · (the leg's ask if price× > 0, else its bid)
    bid (sell the spread now) = Σ price× · (the leg's bid if price× > 0, else its ask)
    size                      = the fewest spread lots any leg has at that price:
                                min over legs of  floor(quantity / (|ratio| · lot size))

Only the top level is implied: deeper levels of different legs don't trade together, so no
depth is invented. A side with a leg missing its price, or with less than one spread lot at
a leg's best price, has no price; legs whose prices are
more than MAX_LEG_SKEW_S apart give no tradable price at all (one of them is stale, #267).
Every price is whole paise; spread prices may be negative (a calendar can be).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from laddrix.algos.spec import AlgoSpec, ParamKind, ParamSpec, check_params
from laddrix.domain.instruments import Instrument
from laddrix.domain.ticks import DepthLevel, Tick

PREFIX = "SPREAD:"  # a spread's id as an instrument: "SPREAD:<id>"
MAX_LEGS = 6
MIN_LEGS = 2
MAX_RATIO = 50
MAX_PAYUP = 50
MAX_NAME = 40
MAX_SPREADS_PER_USER = 50
MAX_LEG_SKEW_S = 3.0  # leg prices further apart in time than this: no tradable price (#267)


@dataclass(frozen=True)
class SpreadLeg:
    instrument_id: str
    ratio: int  # lots of this leg per spread lot; + bought when the spread is bought
    multiplier: int  # how much its price counts in the spread's price
    quote: bool = False  # the leg that rests a quote
    payup_ticks: int = 2  # how far past the best price its hedge may go


@dataclass(frozen=True)
class Controls:
    """How the Autospreader trades this spread (#425); the screens draw them from CONTROLS."""

    requote_ticks: int = 1
    max_unhedged_lots: int = 0  # spread lots; 0: no limit
    chase_ticks: int = 0
    hedge_timeout_s: float = 5.0


CONTROLS = AlgoSpec(
    code="CONTROLS",
    name="Autospreader controls",
    help="How the Autospreader trades this spread",
    params=(
        ParamSpec("requote_ticks", "Re-quote threshold", ParamKind.TICKS,
                  "Ticks the quote's target must move before the quote is moved (fewer "
                  "modifications); a move that would make the spread worse is made at once",
                  required=False, default=1, min=1, max=50),
        ParamSpec("max_unhedged_lots", "Max unhedged", ParamKind.COUNT,
                  "The most spread lots one quote may carry, so one fill never leaves more "
                  "than this unhedged (0: no limit)", required=False, default=0, min=0, max=1000),
        ParamSpec("chase_ticks", "Chase", ParamKind.TICKS,
                  "Ticks a working hedge may follow the market past its first price "
                  "(0: it stays at its best ± pay-up)", required=False, default=0, min=0, max=50),
        ParamSpec("hedge_timeout_s", "Hedge timeout", ParamKind.SECONDS,
                  "Seconds a leg may stay unhedged before an alert; no new quote meanwhile",
                  required=False, default=5, min=1, max=300),
    ),
)  # fmt: skip


def controls_from_dict(raw: dict[str, Any] | None) -> tuple[Controls, list[str]]:
    """Controls from the screens or a file (missing ones: their defaults), with the problems in
    words."""
    clean, problems = check_params(CONTROLS, dict(raw or {}))
    if problems:
        return Controls(), problems
    return Controls(**clean), []


def controls_to_dict(c: Controls) -> dict[str, Any]:
    return {"requote_ticks": c.requote_ticks, "max_unhedged_lots": c.max_unhedged_lots,
            "chase_ticks": c.chase_ticks, "hedge_timeout_s": c.hedge_timeout_s}  # fmt: skip


@dataclass(frozen=True)
class SpreadDef:
    spread_id: str
    user_id: str
    workspace: str
    name: str
    legs: tuple[SpreadLeg, ...]
    created_at: datetime
    updated_at: datetime
    controls: Controls = Controls()  # how the Autospreader trades it (#425)

    @property
    def instrument_id(self) -> str:
        """The spread as an instrument, for screens and streams."""
        return PREFIX + self.spread_id

    @property
    def quote_leg(self) -> SpreadLeg:
        """The first quoting leg: the Autospreader's parent order is on it."""
        return next(leg for leg in self.legs if leg.quote)

    @property
    def quote_legs(self) -> tuple[SpreadLeg, ...]:
        return tuple(leg for leg in self.legs if leg.quote)


def check(
    name: str, legs: list[SpreadLeg], lookup: Callable[[str], Instrument | None],
    expired: Callable[[Instrument], bool] = lambda _i: False,
) -> list[str]:  # fmt: skip
    """Every problem with a definition, in words (empty: it is fine)."""
    problems: list[str] = []
    clean = name.strip()
    if not clean or len(clean) > MAX_NAME:
        problems.append(f"the name must be 1 to {MAX_NAME} characters")
    if not MIN_LEGS <= len(legs) <= MAX_LEGS:
        problems.append(f"a spread has {MIN_LEGS} to {MAX_LEGS} legs")
    ids = [leg.instrument_id for leg in legs]
    if len(set(ids)) != len(ids):
        problems.append("each leg must be a different contract")
    for n, leg in enumerate(legs, start=1):
        instrument = lookup(leg.instrument_id)
        if instrument is None:
            problems.append(f"leg {n}: {leg.instrument_id} is not a known contract")
        elif not instrument.tradable:
            problems.append(f"leg {n}: {leg.instrument_id} can't be traded")
        elif expired(instrument):
            problems.append(f"leg {n}: {leg.instrument_id} has expired")
        if leg.ratio == 0 or abs(leg.ratio) > MAX_RATIO:
            problems.append(f"leg {n}: the ratio must be a whole number from 1 to {MAX_RATIO}, "
                            "+ or −")  # fmt: skip
        if leg.multiplier == 0 or abs(leg.multiplier) > MAX_RATIO:
            problems.append(f"leg {n}: the price multiplier must be a whole number from 1 to "
                            f"{MAX_RATIO}, + or −")  # fmt: skip
        elif leg.ratio and (leg.ratio > 0) != (leg.multiplier > 0):
            problems.append(f"leg {n}: the price multiplier must have the ratio's sign (a leg "
                            "bought with the spread counts + in its price)")  # fmt: skip
        if not 0 <= leg.payup_ticks <= MAX_PAYUP:
            problems.append(f"leg {n}: pay-up must be 0 to {MAX_PAYUP} ticks")
    if not any(leg.quote for leg in legs):
        problems.append("choose at least one leg to quote")
    return problems


def spread_tick(legs: tuple[SpreadLeg, ...], lookup: Callable[[str], Instrument | None]) -> int:
    """The spread's price step: every leg moving by its own tick moves the spread by a
    multiple of this (the gcd of |price×| · leg tick)."""
    step = 0
    for leg in legs:
        instrument = lookup(leg.instrument_id)
        if instrument is not None:
            step = math.gcd(step, abs(leg.multiplier) * instrument.tick_paise)
    return step or 1


@dataclass(frozen=True)
class Implied:
    """What the spread trades at now, from its legs' prices."""

    bid: DepthLevel | None  # sell the spread now: the price and how many spread lots
    ask: DepthLevel | None  # buy the spread now
    ltp_paise: int | None  # Σ price× · leg last price (a mark, not a trade)
    stale: str  # why there is no tradable price (empty: there is one, or a leg has no price)
    received_at: datetime | None


def implied(
    spread: SpreadDef,
    latest: Callable[[str], Tick | None],
    lookup: Callable[[str], Instrument | None],
    age_s: Callable[[str], float | None] = lambda _i: None,
) -> Implied:
    ticks = [latest(leg.instrument_id) for leg in spread.legs]
    have = [t for t in ticks if t is not None]
    received = max((t.received_at for t in have if t.received_at), default=None)
    ltp = (sum(leg.multiplier * t.ltp_paise for leg, t in zip(spread.legs, ticks, strict=True)
               if t is not None) if len(have) == len(ticks) else None)  # fmt: skip
    if len(have) < len(ticks):
        return Implied(None, None, ltp, "", received)
    ages = [a for a in (age_s(leg.instrument_id) for leg in spread.legs) if a is not None]
    if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:
        skew = f"leg prices {max(ages) - min(ages):.0f} s apart"
        return Implied(None, None, ltp, skew, received)

    def side(buying: bool) -> DepthLevel | None:
        price = 0
        lots: int | None = None
        for leg, tick in zip(spread.legs, ticks, strict=True):
            assert tick is not None
            # buying the spread buys the + legs (at their ask), sells the − legs (at their bid)
            level = tick.best_ask if (leg.multiplier > 0) == buying else tick.best_bid
            instrument = lookup(leg.instrument_id)
            if level is None or instrument is None:
                return None
            price += leg.multiplier * level.price_paise
            here = level.qty // (abs(leg.ratio) * instrument.lot_size)
            lots = here if lots is None else min(lots, here)
        return DepthLevel(price, lots or 0, 0) if lots else None

    return Implied(side(False), side(True), ltp, "", received)


def as_tick(spread: SpreadDef, now: Implied) -> Tick:
    """The implied price as a tick of the spread "instrument", for screens and streams."""
    return Tick(
        spread.instrument_id,
        now.ltp_paise or 0,
        received_at=now.received_at,
        bids=(now.bid,) if now.bid else (),
        asks=(now.ask,) if now.ask else (),
    )


def leg_to_dict(leg: SpreadLeg) -> dict[str, Any]:
    return {"instrument_id": leg.instrument_id, "ratio": leg.ratio, "multiplier": leg.multiplier,
            "quote": leg.quote, "payup_ticks": leg.payup_ticks}  # fmt: skip


def leg_from_dict(d: dict[str, Any]) -> SpreadLeg:
    return SpreadLeg(str(d["instrument_id"]), int(d["ratio"]), int(d["multiplier"]),
                     bool(d.get("quote", False)), int(d.get("payup_ticks", 2)))  # fmt: skip


def spread_to_dict(s: SpreadDef) -> dict[str, Any]:
    return {
        "spread_id": s.spread_id,
        "instrument_id": s.instrument_id,
        "user_id": s.user_id,
        "workspace": s.workspace,
        "name": s.name,
        "legs": [leg_to_dict(x) for x in s.legs],
        "controls": controls_to_dict(s.controls),
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def spread_from_dict(d: dict[str, Any]) -> SpreadDef:
    controls, problems = controls_from_dict(d.get("controls"))  # files from before #425: none
    if problems:
        raise ValueError("; ".join(problems))
    return SpreadDef(
        str(d["spread_id"]), str(d["user_id"]), str(d["workspace"]), str(d["name"]),
        tuple(leg_from_dict(x) for x in d["legs"]),
        datetime.fromisoformat(d["created_at"]), datetime.fromisoformat(d["updated_at"]),
        controls,
    )  # fmt: skip


def renamed(s: SpreadDef, name: str, legs: tuple[SpreadLeg, ...], now: datetime) -> SpreadDef:
    return replace(s, name=name.strip(), legs=legs, updated_at=now)
