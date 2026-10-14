"""Orders: what a user asks for, and the updates that describe its life.

The vocabulary follows what Indian brokers (Kite included) use, so adapters
map almost one-to-one, but nothing here depends on a specific broker.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from mocktrade.domain.instruments import Instrument, InstrumentId

CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9]{1,20}")  # fits a broker tag (Kite: alphanumeric, ≤ 20)


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for buys, -1 for sells: position change = sign × quantity."""
        return 1 if self is Side.BUY else -1


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"  # stop-loss limit: triggers at trigger price, then works as a limit
    SL_M = "SL-M"  # stop-loss market: triggers, then works as a protected market order


class Product(Enum):
    CNC = "CNC"  # delivery (cash & carry), equity
    MIS = "MIS"  # intraday, squared off by the broker
    NRML = "NRML"  # overnight futures & options


class Validity(Enum):
    DAY = "DAY"
    IOC = "IOC"  # immediate or cancel


class OrderStatus(Enum):
    PENDING = "PENDING"  # sent, broker hasn't confirmed yet
    OPEN = "OPEN"  # working at the exchange
    PARTIAL = "PARTIAL"  # partly filled, rest still working
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def is_final(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)


@dataclass(frozen=True)
class OrderRequest:
    """One new order, as a user (or an algo acting for a user) asks for it."""

    client_order_id: str  # our id, also sent to the broker as its tag
    account_id: str  # whose broker account
    user_id: str  # who asked (for audit and permissions)
    instrument_id: InstrumentId
    side: Side
    qty: int
    order_type: OrderType
    product: Product
    validity: Validity = Validity.DAY
    price_paise: int | None = None
    trigger_paise: int | None = None
    market_protection: Decimal | None = None  # percent; -1 = broker's automatic protection
    algo_id: str | None = None  # exchange algo id when the order comes from an algo
    parent_id: str | None = None  # the algo's parent order, if any

    def validate(self, instrument: Instrument) -> None:
        """Raise ValueError if this request breaks any order or exchange rule."""
        if not CLIENT_ORDER_ID.fullmatch(self.client_order_id):
            raise ValueError("client order id must be 1-20 letters or digits")
        if instrument.id != self.instrument_id:
            raise ValueError(f"instrument mismatch: {instrument.id} vs {self.instrument_id}")
        if not instrument.tradable:
            raise ValueError(f"{instrument.id} can't be traded")
        instrument.check_quantity(self.qty)

        needs_price = self.order_type in (OrderType.LIMIT, OrderType.SL)
        needs_trigger = self.order_type in (OrderType.SL, OrderType.SL_M)
        needs_protection = self.order_type in (OrderType.MARKET, OrderType.SL_M)

        if needs_price:
            if self.price_paise is None:
                raise ValueError(f"{self.order_type.value} orders need a price")
            instrument.check_price(self.price_paise)
        elif self.price_paise is not None:
            raise ValueError(f"{self.order_type.value} orders must not have a price")

        if needs_trigger:
            if self.trigger_paise is None:
                raise ValueError(f"{self.order_type.value} orders need a trigger price")
            instrument.check_price(self.trigger_paise)
        elif self.trigger_paise is not None:
            raise ValueError(f"{self.order_type.value} orders must not have a trigger price")

        if needs_protection:
            p = self.market_protection
            if p is None or not (p == -1 or 0 < p <= 100):
                raise ValueError("market protection must be -1 (automatic) or between 0 and 100")
        elif self.market_protection is not None:
            raise ValueError("market protection only applies to MARKET and SL-M orders")


@dataclass(frozen=True)
class OrderUpdate:
    """What a broker reports about one order. Every broker adapter emits these."""

    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    filled_qty: int = 0
    avg_price_paise: int = 0
    last_fill_qty: int = 0  # size of the newest fill (0 if this update has no fill)
    last_fill_price_paise: int = 0
    reason: str = ""  # why it was rejected or cancelled
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
