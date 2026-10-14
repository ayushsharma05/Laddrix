"""The instrument master: every instrument by our id, plus each broker's id for it.

Brokers number instruments their own way (Kite: instrument_token). Those numbers
live only here, as a two-way mapping per broker, so the rest of MockTrade only
ever sees our ids ("NSE:RELIANCE").
"""

from mocktrade.domain.instruments import Instrument, InstrumentId


class InstrumentStore:
    def __init__(self) -> None:
        self._by_id: dict[InstrumentId, Instrument] = {}
        self._to_ours: dict[tuple[str, str], InstrumentId] = {}  # (broker, broker ref) -> our id
        self._to_broker: dict[tuple[str, InstrumentId], str] = {}  # (broker, our id) -> broker ref

    def __len__(self) -> int:
        return len(self._by_id)

    def add(self, instrument: Instrument) -> None:
        """Add or replace an instrument (the daily refresh replaces yesterday's)."""
        self._by_id[instrument.id] = instrument

    def get(self, instrument_id: InstrumentId) -> Instrument | None:
        return self._by_id.get(instrument_id)

    def map_broker(self, broker: str, broker_ref: str, instrument_id: InstrumentId) -> None:
        """Record that `broker` calls our `instrument_id` by `broker_ref`."""
        if instrument_id not in self._by_id:
            raise KeyError(f"unknown instrument {instrument_id}")
        old_ref = self._to_broker.get((broker, instrument_id))
        if old_ref is not None and old_ref != broker_ref:
            del self._to_ours[(broker, old_ref)]
        self._to_ours[(broker, broker_ref)] = instrument_id
        self._to_broker[(broker, instrument_id)] = broker_ref

    def from_broker(self, broker: str, broker_ref: str) -> InstrumentId | None:
        """Our id for a broker's identifier, or None if we don't know it."""
        return self._to_ours.get((broker, broker_ref))

    def to_broker(self, broker: str, instrument_id: InstrumentId) -> str | None:
        """The broker's identifier for our id, or None if that broker doesn't list it."""
        return self._to_broker.get((broker, instrument_id))

    def search(self, text: str, limit: int = 20) -> list[Instrument]:
        """Instruments whose symbol starts with `text` (case-insensitive), shortest first."""
        needle = text.strip().upper()
        if not needle:
            return []
        found = [i for i in self._by_id.values() if i.symbol.upper().startswith(needle)]
        found.sort(key=lambda i: (len(i.symbol), i.symbol, i.exchange.value))
        return found[:limit]
