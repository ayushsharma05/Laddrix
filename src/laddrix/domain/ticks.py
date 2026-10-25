"""Ticks: one snapshot of an instrument's market, in our own ids and whole paise."""

from datetime import datetime
from typing import NamedTuple

from laddrix.domain.instruments import InstrumentId


class DepthLevel(NamedTuple):
    """One price level of market depth: what's waiting to trade at one price.

    A NamedTuple, not a dataclass: immutable all the same, and five times cheaper to make
    (a full Kite tick carries ten of them; thousands of ticks a second)."""

    price_paise: int
    qty: int
    orders: int


class Tick(NamedTuple):
    """The latest known state of one instrument's market.

    Fields a feed doesn't provide (e.g. depth in a light mode) are None or empty.
    Bids are best (highest) first, asks best (lowest) first.

    A NamedTuple, not a dataclass: immutable all the same, and about four times cheaper to
    make. Every price change makes one, thousands a second (docs/design/performance.md).
    """

    instrument_id: InstrumentId
    ltp_paise: int  # last traded price
    received_at: datetime | None = None  # when our server got it (feeds set it, per frame)
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
