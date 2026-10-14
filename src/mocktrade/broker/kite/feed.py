"""Glue between Kite's ticker and MockTrade: Kite tokens in, our ticks out.

This is the "normaliser" box of the one-chart: it translates Kite's instrument
tokens into our instrument ids, so the market data cache never sees a Kite number.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from mocktrade.broker.kite.decoder import KiteTick
from mocktrade.broker.kite.instruments import BROKER
from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore

log = logging.getLogger(__name__)


def to_tick(k: KiteTick, instrument_id: InstrumentId, received_at: datetime | None = None) -> Tick:
    # positional, in Tick's field order: this runs for every price change
    return Tick(
        instrument_id,
        k.ltp_paise,
        received_at,
        k.last_qty,
        k.avg_price_paise,
        k.volume,
        k.buy_qty,
        k.sell_qty,
        k.open_paise,
        k.high_paise,
        k.low_paise,
        k.close_paise,
        k.open_interest,
        k.last_trade_time,
        k.exchange_time,
        k.bids,
        k.asks,
    )


class KiteFeed:
    """Turns Kite ticks into our ticks and maps our ids to Kite tokens for subscribing."""

    def __init__(self, store: InstrumentStore, market_data: MarketData) -> None:
        self._store = store
        self._market_data = market_data
        self.unknown_tokens = 0

    def on_ticks(self, kite_ticks: list[KiteTick]) -> None:
        received = datetime.now(UTC)  # one frame, one arrival time
        on_tick, from_broker = self._market_data.on_tick, self._store.from_broker
        for kite_tick in kite_ticks:
            instrument_id = from_broker(BROKER, str(kite_tick.token))
            if instrument_id is None:
                self.unknown_tokens += 1
                continue
            on_tick(to_tick(kite_tick, instrument_id, received))

    def tokens_for(self, instrument_ids: Iterable[InstrumentId]) -> list[int]:
        """Kite tokens for our ids. Raises KeyError naming any id Kite doesn't list."""
        tokens = []
        for instrument_id in instrument_ids:
            ref = self._store.to_broker(BROKER, instrument_id)
            if ref is None:
                raise KeyError(f"Kite has no token for {instrument_id}")
            tokens.append(int(ref))
        return tokens
