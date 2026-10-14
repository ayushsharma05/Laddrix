"""Glue between Kite's ticker and MockTrade: Kite tokens in, our ticks out.

This is the "normaliser" box of the one-chart: it translates Kite's instrument
tokens into our instrument ids, so the market data cache never sees a Kite number.
"""

import logging
from collections.abc import Iterable

from mocktrade.broker.kite.decoder import KiteTick
from mocktrade.broker.kite.instruments import BROKER
from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore

log = logging.getLogger(__name__)


def to_tick(kite_tick: KiteTick, instrument_id: InstrumentId) -> Tick:
    return Tick(
        instrument_id=instrument_id,
        ltp_paise=kite_tick.ltp_paise,
        last_qty=kite_tick.last_qty,
        avg_price_paise=kite_tick.avg_price_paise,
        volume=kite_tick.volume,
        buy_qty=kite_tick.buy_qty,
        sell_qty=kite_tick.sell_qty,
        open_paise=kite_tick.open_paise,
        high_paise=kite_tick.high_paise,
        low_paise=kite_tick.low_paise,
        close_paise=kite_tick.close_paise,
        open_interest=kite_tick.open_interest,
        last_trade_time=kite_tick.last_trade_time,
        exchange_time=kite_tick.exchange_time,
        bids=kite_tick.bids,
        asks=kite_tick.asks,
    )


class KiteFeed:
    """Turns Kite ticks into our ticks and maps our ids to Kite tokens for subscribing."""

    def __init__(self, store: InstrumentStore, market_data: MarketData) -> None:
        self._store = store
        self._market_data = market_data
        self.unknown_tokens = 0

    def on_ticks(self, kite_ticks: list[KiteTick]) -> None:
        for kite_tick in kite_ticks:
            instrument_id = self._store.from_broker(BROKER, str(kite_tick.token))
            if instrument_id is None:
                self.unknown_tokens += 1
                continue
            self._market_data.on_tick(to_tick(kite_tick, instrument_id))

    def tokens_for(self, instrument_ids: Iterable[InstrumentId]) -> list[int]:
        """Kite tokens for our ids. Raises KeyError naming any id Kite doesn't list."""
        tokens = []
        for instrument_id in instrument_ids:
            ref = self._store.to_broker(BROKER, instrument_id)
            if ref is None:
                raise KeyError(f"Kite has no token for {instrument_id}")
            tokens.append(int(ref))
        return tokens
