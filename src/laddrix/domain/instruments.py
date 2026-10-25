"""Instruments: what can be traded, identified by OUR ids, not any broker's.

An instrument id is "EXCHANGE:SYMBOL", e.g. "NSE:RELIANCE" or "NFO:NIFTY26OCTFUT".
Each broker's own numbers (Kite's instrument_token, for example) live in the
reference-data mapping, so nothing else in Laddrix depends on one broker.
"""

from dataclasses import dataclass
from datetime import date
from enum import Enum

InstrumentId = str


class Exchange(Enum):
    """Where an instrument trades. Values match the codes Indian brokers use."""

    NSE = "NSE"  # NSE cash market (shares)
    BSE = "BSE"  # BSE cash market
    NFO = "NFO"  # NSE futures & options
    BFO = "BFO"  # BSE futures & options


class InstrumentKind(Enum):
    """What sort of thing it is."""

    EQUITY = "EQ"
    FUTURE = "FUT"
    CALL = "CE"
    PUT = "PE"
    INDEX = "INDEX"  # e.g. NIFTY 50: has prices, can't be traded


def make_instrument_id(exchange: Exchange, symbol: str) -> InstrumentId:
    """Build our id for an instrument: "NSE:RELIANCE"."""
    return f"{exchange.value}:{symbol}"


@dataclass(frozen=True)
class Instrument:
    """One instrument and the price and size rules the exchange enforces for it."""

    exchange: Exchange
    symbol: str  # the exchange trading symbol, e.g. "RELIANCE", "NIFTY26OCTFUT"
    kind: InstrumentKind
    tick_paise: int  # smallest price step, in paise (0.05 rupees -> 5)
    lot_size: int  # quantities must be a multiple of this (1 for shares)
    expiry: date | None = None  # futures and options only
    strike_paise: int | None = None  # options only
    underlying: str | None = None  # e.g. "NIFTY" for its futures and options
    # the exchange's most per order (its "freeze quantity", #424); None: not known. Kite's
    # instrument list doesn't carry it: it comes from a file (refdata/freeze.py)
    freeze_qty: int | None = None

    def __post_init__(self) -> None:
        if self.tick_paise <= 0:
            raise ValueError(f"{self.symbol}: tick must be positive")
        if self.lot_size <= 0:
            raise ValueError(f"{self.symbol}: lot size must be positive")
        if self.freeze_qty is not None and self.freeze_qty < self.lot_size:
            raise ValueError(f"{self.symbol}: freeze quantity must be at least one lot")

    @property
    def id(self) -> InstrumentId:
        """Our id for this instrument."""
        return make_instrument_id(self.exchange, self.symbol)

    @property
    def tradable(self) -> bool:
        """Indices have prices but can't be bought or sold."""
        return self.kind is not InstrumentKind.INDEX

    def check_price(self, price_paise: int) -> None:
        """Raise ValueError unless the price is positive and on the tick grid."""
        if price_paise <= 0:
            raise ValueError(f"price must be positive, got {price_paise} paise")
        if price_paise % self.tick_paise != 0:
            raise ValueError(
                f"{price_paise} paise is not a multiple of {self.symbol}'s tick "
                f"of {self.tick_paise} paise"
            )

    def check_quantity(self, qty: int) -> None:
        """Raise ValueError unless the quantity is positive and a whole number of lots."""
        if qty <= 0:
            raise ValueError(f"quantity must be positive, got {qty}")
        if qty % self.lot_size != 0:
            raise ValueError(f"{qty} is not a multiple of {self.symbol}'s lot size {self.lot_size}")

    def check_freeze(self, qty: int) -> None:
        """Raise ValueError if one order of `qty` is above the exchange's freeze quantity
        (the exchange refuses it; bigger quantities go in several orders)."""
        if self.freeze_qty is not None and qty > self.freeze_qty:
            raise ValueError(
                f"{qty} is more than {self.symbol}'s freeze quantity {self.freeze_qty}, "
                "the exchange's most per order: split it"
            )

    @property
    def max_order_qty(self) -> int | None:
        """The most one order may carry by the exchange's rule (a whole number of lots), or
        None when no limit is known."""
        if self.freeze_qty is None:
            return None
        return self.freeze_qty - self.freeze_qty % self.lot_size
