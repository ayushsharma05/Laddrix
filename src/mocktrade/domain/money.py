"""Money and prices as whole paise.

Brokers send prices as whole paise (2501.05 rupees arrives as 250105), so every
price inside MockTrade is an int of paise. We convert only at the edges.
"""

from decimal import Decimal, InvalidOperation

PAISE_PER_RUPEE = 100


def to_paise(rupees: str | Decimal) -> int:
    """Turn a rupee amount like "2501.05" into whole paise (250105).

    Raises ValueError if it isn't a number or has fractions of a paisa.
    """
    try:
        exact = Decimal(str(rupees))
    except InvalidOperation:
        raise ValueError(f"not an amount: {rupees!r}") from None
    if not exact.is_finite():
        raise ValueError(f"not an amount: {rupees!r}")
    paise = exact * PAISE_PER_RUPEE
    if paise != paise.to_integral_value():
        raise ValueError(f"{rupees} has fractions of a paisa")
    return int(paise)


def to_rupees(paise: int) -> Decimal:
    """Turn whole paise back into rupees for people to read (250105 -> 2501.05)."""
    return Decimal(paise) / PAISE_PER_RUPEE
