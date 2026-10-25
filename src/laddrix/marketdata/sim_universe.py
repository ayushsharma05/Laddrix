"""The contracts the market simulator prices, with their starting prices (paise).

Used by the demo and by the server in simulated-price mode (paper trading without Kite).

The futures and options roll with the calendar (#290): the "near" month is the first monthly
expiry that hasn't ended yet, the "next" month the one after. NSE's monthly index contracts
expire on the month's last Tuesday (checked 23 Oct 2026; a holiday moves it a day earlier,
which the demo ignores). So the demo never runs on contracts that have expired.
"""

import calendar
import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind

IST = ZoneInfo("Asia/Kolkata")
K = InstrumentKind


def last_tuesday(year: int, month: int) -> date:
    """The month's last Tuesday: the monthly expiry day."""
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - calendar.TUESDAY) % 7)


def monthly_expiries(now: datetime) -> tuple[date, date]:
    """The near and next monthly expiries at `now`: an expiry has ended at 15:30 that day."""
    today = now.astimezone(IST)
    year, month = today.year, today.month
    found: list[date] = []
    while len(found) < 2:
        expiry = last_tuesday(year, month)
        if datetime.combine(expiry, time(15, 30), tzinfo=IST) > today:
            found.append(expiry)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found[0], found[1]


def _code(expiry: date) -> str:
    """Kite's symbol part for a monthly contract: 27 Oct 2026 -> "26OCT"."""
    return f"{expiry:%y}{calendar.month_abbr[expiry.month].upper()}"


def _near_override() -> date | None:
    """LADDRIX_SIM_NEAR_EXPIRY=2026-10-23: the near contracts expire that day (to try an
    expiry day on the demo, #290)."""
    text = os.environ.get("LADDRIX_SIM_NEAR_EXPIRY", "").strip()
    return date.fromisoformat(text) if text else None


NEAR, NEXT = monthly_expiries(datetime.now(UTC))
NEAR = _near_override() or NEAR
EXPIRY = NEAR  # the near month (kept under its old name)
INSTRUMENTS: list[tuple[Instrument, int]] = [
    (
        Instrument(Exchange.NFO, f"NIFTY{_code(NEAR)}FUT", K.FUTURE, 10, 75, NEAR, None, "NIFTY"),
        2501000,
    ),
    (
        Instrument(Exchange.NFO, f"NIFTY{_code(NEXT)}FUT", K.FUTURE, 10, 75, NEXT, None, "NIFTY"),
        2514860,
    ),
    (
        Instrument(
            Exchange.NFO, f"BANKNIFTY{_code(NEAR)}FUT", K.FUTURE, 20, 35, NEAR, None, "BANKNIFTY"
        ),
        5621460,
    ),
    (
        Instrument(
            Exchange.NFO, f"NIFTY{_code(NEAR)}25000CE", K.CALL, 5, 75, NEAR, 2500000, "NIFTY"
        ),
        12005,
    ),
    (
        Instrument(
            Exchange.NFO, f"NIFTY{_code(NEAR)}25000PE", K.PUT, 5, 75, NEAR, 2500000, "NIFTY"
        ),
        9540,
    ),
    (Instrument(Exchange.NSE, "RELIANCE", K.EQUITY, 10, 1), 140020),
    (Instrument(Exchange.NSE, "HDFCBANK", K.EQUITY, 10, 1), 174280),
    (Instrument(Exchange.NSE, "INFY", K.EQUITY, 10, 1), 150340),
    (Instrument(Exchange.NSE, "SBIN", K.EQUITY, 5, 1), 87325),
    (Instrument(Exchange.NSE, "ICICIBANK", K.EQUITY, 10, 1), 128600),
]


def _nifty_options() -> list[tuple[Instrument, int]]:
    """A NIFTY option chain for the simulator: the near month (100-point strikes, 24,000 to
    26,000) and the next (200-point strikes). The simulator prices them from the futures, so the
    start price here only has to sit on the tick grid."""
    out: list[tuple[Instrument, int]] = []
    for expiry, step in ((NEAR, 100), (NEXT, 200)):
        code = _code(expiry)
        for strike in range(24000, 26001, step):
            for kind in (K.CALL, K.PUT):
                symbol = f"NIFTY{code}{strike}{kind.value}"
                if any(i.symbol == symbol for i, _ in INSTRUMENTS):
                    continue
                out.append(
                    (
                        Instrument(
                            Exchange.NFO, symbol, kind, 5, 75, expiry, strike * 100, "NIFTY"
                        ),
                        500,
                    )
                )
    return out


INSTRUMENTS += _nifty_options()

# Freeze quantities for the simulated contracts (#424): the most one order may carry.
# Sample values in the shape NSE publishes; a real server reads them from a file
# (LADDRIX_FREEZE_FILE), and they must be checked against NSE's latest list before trading.
FREEZE: dict[str, int] = {"NIFTY": 1800, "BANKNIFTY": 900}
