"""Risk limits for one broker account: the numbers the pre-trade checks enforce.

Money is in paise, like every price in MockTrade. Defaults suit a careful personal
account; an admin screen (K5) will edit them per account.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from mocktrade.domain.instruments import Exchange, InstrumentKind
from mocktrade.domain.orders import Product

K = InstrumentKind

# Which products each kind of instrument can use at Indian brokers.
PRODUCTS_FOR_KIND: dict[InstrumentKind, frozenset[Product]] = {
    K.EQUITY: frozenset({Product.CNC, Product.MIS}),
    K.FUTURE: frozenset({Product.NRML, Product.MIS}),
    K.CALL: frozenset({Product.NRML, Product.MIS}),
    K.PUT: frozenset({Product.NRML, Product.MIS}),
}


@dataclass(frozen=True)
class RiskLimits:
    allowed_exchanges: frozenset[Exchange] = frozenset(Exchange)
    allowed_kinds: frozenset[InstrumentKind] = frozenset({K.EQUITY, K.FUTURE, K.CALL, K.PUT})
    blocked_instruments: frozenset[str] = frozenset()  # our ids, e.g. "NSE:YESBANK"
    allow_option_selling: bool = False  # selling options opens unlimited-loss positions

    max_order_lots: int = 10
    max_order_value_paise: int = 50_00_000_00  # ₹50 lakh
    max_position_lots: int = 20  # per instrument, long or short
    max_open_orders: int = 50

    # How far a price may be from the last traded price, by instrument kind (percent).
    price_band_pct: dict[InstrumentKind, Decimal] = field(
        default_factory=lambda: {
            K.EQUITY: Decimal("5"),
            K.FUTURE: Decimal("5"),
            K.CALL: Decimal("40"),  # cheap options move tens of percent in minutes
            K.PUT: Decimal("40"),
        }
    )
    require_market_price: bool = True  # refuse orders we can't check against a live price

    max_daily_loss_paise: int = 20_000_00  # ₹20,000; beyond it only closing trades
    duplicate_window_s: float = 1.0  # identical manual orders this close are a double click

    def __post_init__(self) -> None:
        for name in ("max_order_lots", "max_order_value_paise", "max_position_lots"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_open_orders <= 0 or self.max_daily_loss_paise <= 0:
            raise ValueError("max_open_orders and max_daily_loss_paise must be positive")
        if any(band <= 0 for band in self.price_band_pct.values()):
            raise ValueError("price bands must be positive")
