"""Paper money: the cash and margin a paper account has, worked out the way a broker would.

A real broker computes SPAN and exposure margin from exchange files; paper can't, so it uses
plain rates (`PaperMargins`, replaceable) and says so (`Funds.estimate`). The cash side is
exact:

    opening   = starting balance + P&L of earlier days − what the holdings cost
    available = opening + today's realised + unrealised P&L − margin used + holdings sold today

Margin used, per instrument and product, is the worst of the position as it is and as it
would be if every working buy, or every working sell, filled: an order that only closes
blocks nothing, one that adds blocks its share (as brokers block margin for open orders).

    CNC (delivery) long       the full value                          "Delivery margin"
    CNC short                 can only be holdings sold today: a credit "Holdings sold today"
    MIS equity                `mis_equity_pct` of the value           "MIS margin"
    future                    `futures_pct` of the contract value     "SPAN + exposure"
    option bought             the premium (its value)                 "Options premium"
    option sold               `short_option_pct` of the strike value  "SPAN + exposure"

A long option's or delivery buy's "margin" is its value, so with its P&L the available cash
moves by exactly what was paid: −qty × price.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from laddrix.domain.instruments import Instrument, InstrumentKind
from laddrix.domain.orders import Product
from laddrix.domain.portfolio import Funds, FundsLine

DELIVERY = "Delivery margin"
MIS = "MIS margin"
SPAN = "SPAN + exposure"
PREMIUM = "Options premium"
USED_ORDER = (SPAN, PREMIUM, DELIVERY, MIS)


@dataclass(frozen=True)
class PaperMargins:
    """Rates in percent. Defaults are deliberately on the high side of what NSE asks."""

    mis_equity_pct: Fraction = Fraction(20)
    futures_pct: Fraction = Fraction(12)
    short_option_pct: Fraction = Fraction(12)

    def margin(
        self, instrument: Instrument, product: Product, qty: int, price: int
    ) -> tuple[str, Fraction]:
        """(line, amount) for holding `qty` (signed) at `price`. A negative amount is a credit."""
        size = abs(qty)
        kind = instrument.kind
        if kind is InstrumentKind.EQUITY:
            if product is Product.CNC:
                return DELIVERY, Fraction(qty * price)  # a short CNC is a sale of holdings
            return MIS, size * price * self.mis_equity_pct / 100
        if kind is InstrumentKind.FUTURE:
            return SPAN, size * price * self.futures_pct / 100
        if qty >= 0:  # an option bought: its premium
            return PREMIUM, Fraction(size * price)
        strike = instrument.strike_paise or price
        return SPAN, size * strike * self.short_option_pct / 100


@dataclass(frozen=True)
class Exposure:
    """One (instrument, product): the position now and what working orders could add."""

    instrument: Instrument
    product: Product
    qty: int
    price: int  # the last price (or the average when no price is known)
    buy_leaves: int = 0
    sell_leaves: int = 0
    buy_price: int = 0  # the price working buys would pay (their limit, else the last price)
    sell_price: int = 0


def used_margin(
    rates: PaperMargins, exposures: Iterable[Exposure]
) -> tuple[dict[str, Fraction], Fraction]:
    """Margin by line, and the credit from holdings sold today."""
    used: dict[str, Fraction] = {}
    credit = Fraction(0)
    for e in exposures:
        # what the position uses now, then if all buys or all sells filled
        cases = [rates.margin(e.instrument, e.product, e.qty, e.price)]
        if e.buy_leaves:
            cases.append(_with(rates, e, e.buy_leaves, e.buy_price or e.price))
        if e.sell_leaves:
            cases.append(_with(rates, e, -e.sell_leaves, e.sell_price or e.price))
        line, amount = max(cases, key=lambda c: c[1])
        if amount < 0:
            credit -= amount
        elif amount:
            used[line] = used.get(line, Fraction(0)) + amount
    return used, credit


def _with(rates: PaperMargins, e: Exposure, more: int, price: int) -> tuple[str, Fraction]:
    """The position after `more` (signed) fills at `price`. Margin is linear in size on one
    side, so: adding = what's held (at the last price) + the new part (at the order price);
    reducing = what's left (at the last price); crossing zero = the far side (order price)."""
    after = e.qty + more
    if e.qty == 0:
        return rates.margin(e.instrument, e.product, after, price)
    if (e.qty > 0) == (more > 0):
        line, held = rates.margin(e.instrument, e.product, e.qty, e.price)
        _, added = rates.margin(e.instrument, e.product, more, price)
        return line, held + added
    if after == 0 or (after > 0) == (e.qty > 0):
        return rates.margin(e.instrument, e.product, after, e.price)
    return rates.margin(e.instrument, e.product, after, price)


def paper_funds(
    *,
    starting_paise: int,
    earlier_pnl_paise: int,
    holdings_cost_paise: int,
    realised_paise: int,
    unrealised_paise: int,
    exposures: Iterable[Exposure],
    rates: PaperMargins,
) -> Funds:
    used, credit = used_margin(rates, exposures)
    opening = starting_paise + earlier_pnl_paise - holdings_cost_paise
    used_total = _round(sum(used.values(), Fraction(0)))
    credit_paise = _round(credit)
    available = opening + realised_paise + unrealised_paise + credit_paise - used_total
    available_lines = [
        FundsLine("Opening balance", opening),
        FundsLine("Realised P&L today", realised_paise),
        FundsLine("Unrealised P&L", unrealised_paise),
    ]
    if credit_paise:
        available_lines.append(FundsLine("Holdings sold today", credit_paise))
    return Funds(
        segment="equity",
        available_paise=available,
        used_paise=used_total,
        opening_paise=opening,
        available_lines=tuple(available_lines),
        used_lines=tuple(
            FundsLine(line, _round(used.get(line, Fraction(0)))) for line in USED_ORDER
        ),
        estimate=True,
    )


def _round(value: Fraction) -> int:
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))
