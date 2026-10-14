"""Rules every broker adapter applies the same way, so paper trading can't be more lenient
than real trading.

Numbers checked against Kite Connect on 14 Oct 2026 (docs/design/kite-architecture.md).
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import Side

MAX_MODIFICATIONS_PER_ORDER = 25
AUTO_MARKET_PROTECTION_PCT = Decimal("2")  # what "-1 (automatic)" means in paper trading


def protected_limit(
    instrument: Instrument, side: Side, ltp_paise: int, protection_pct: Decimal
) -> int:
    """The worst price a protected market order may trade at, on the tick grid.

    Buys may pay up to LTP + protection %, rounded down to a tick; sells may receive
    down to LTP - protection %, rounded up to a tick. -1 means the automatic value.
    """
    pct = AUTO_MARKET_PROTECTION_PCT if protection_pct == -1 else protection_pct
    tick = instrument.tick_paise
    if side is Side.BUY:
        raw = Decimal(ltp_paise) * (100 + pct) / 100
        ticks = (raw / tick).to_integral_value(rounding=ROUND_FLOOR)
    else:
        raw = Decimal(ltp_paise) * (100 - pct) / 100
        ticks = (raw / tick).to_integral_value(rounding=ROUND_CEILING)
    return max(int(ticks) * tick, tick)
