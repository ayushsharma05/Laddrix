"""Money and prices as whole paise.

Brokers send prices as whole paise (2501.05 rupees arrives as 250105), so every
price inside Laddrix is an int of paise. We convert only at the edges.
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


def format_rupees(paise: int) -> str:
    """Rupees the way Indian traders read them: lakh and crore grouping, always paise.

    5627250_00 paise -> "₹56,27,250.00"; -2000000 -> "-₹20,000.00".
    """
    sign = "-" if paise < 0 else ""
    rupees, rest = divmod(abs(paise), PAISE_PER_RUPEE)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}.{rest:02d}"
