"""Ticks: one snapshot of an instrument's market, in our own ids and whole paise."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from mocktrade.domain.instruments import InstrumentId


@dataclass(frozen=True)
class DepthLevel:
    """One price level of market depth: what's waiting to trade at one price."""

    price_paise: int
    qty: int
    orders: int


@dataclass(frozen=True)
class Tick:
    """The latest known state of one instrument's market.

    Fields a feed doesn't provide (e.g. depth in a light mode) are None or empty.
    Bids are best (highest) first, asks best (lowest) first.
    """

    instrument_id: InstrumentId
    ltp_paise: int  # last traded price
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_qty: int | None = None
    avg_price_paise: int | None = None
    volume: int | None = None  # total traded today
    buy_qty: int | None = None  # total quantity waiting on the bid side
    sell_qty: int | None = None
    open_paise: int | None = None
    high_paise: int | None = None
    low_paise: int | None = None
    close_paise: int | None = None  # previous day's close
    open_interest: int | None = None
    last_trade_time: datetime | None = None
    exchange_time: datetime | None = None
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()

    @property
    def best_bid(self) -> DepthLevel | None:
        return next((level for level in self.bids if level.qty > 0), None)

    @property
    def best_ask(self) -> DepthLevel | None:
        return next((level for level in self.asks if level.qty > 0), None)
