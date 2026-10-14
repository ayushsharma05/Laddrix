"""The market data cache and its subscriptions.

Prices are state, not history: for each instrument we keep only the newest tick.
Subscribers (screens, algos, the paper broker, P&L) receive changes through their
own Subscription. A subscriber that reads slowly is never flooded: if three ticks
for an instrument arrive before it reads, it gets only the newest one
("conflation", newest wins). Fast and slow subscribers never slow each other down.
"""

import asyncio
from collections.abc import Iterable

from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.ticks import Tick


class Subscription:
    """One subscriber's view: the instruments it watches and the ticks it hasn't read yet."""

    def __init__(self, service: "MarketData", instrument_ids: Iterable[InstrumentId]) -> None:
        self._service = service
        self.instrument_ids: set[InstrumentId] = set(instrument_ids)
        self._pending: dict[InstrumentId, Tick] = {}
        self._ready = asyncio.Event()
        self.closed = False

    def _offer(self, tick: Tick) -> None:
        self._pending[tick.instrument_id] = tick  # replaces any unread older tick
        self._ready.set()

    def add(self, instrument_ids: Iterable[InstrumentId]) -> None:
        ids = set(instrument_ids) - self.instrument_ids
        self.instrument_ids |= ids
        self._service._index(self, ids)
        for instrument_id in ids:  # send the current state straight away
            latest = self._service.latest(instrument_id)
            if latest is not None:
                self._offer(latest)

    def remove(self, instrument_ids: Iterable[InstrumentId]) -> None:
        ids = set(instrument_ids) & self.instrument_ids
        self.instrument_ids -= ids
        self._service._unindex(self, ids)
        for instrument_id in ids:
            self._pending.pop(instrument_id, None)

    def drain(self) -> list[Tick]:
        """Take every unread tick now (newest per instrument), without waiting."""
        ticks = list(self._pending.values())
        self._pending.clear()
        self._ready.clear()
        return ticks

    async def next_batch(self) -> list[Tick]:
        """Wait until something changes, then take every unread tick."""
        while not self._pending:
            if self.closed:
                return []
            self._ready.clear()
            await self._ready.wait()
        return self.drain()

    def close(self) -> None:
        self._service._unindex(self, self.instrument_ids)
        self.closed = True
        self._ready.set()


class MarketData:
    def __init__(self) -> None:
        self._latest: dict[InstrumentId, Tick] = {}
        self._watchers: dict[InstrumentId, set[Subscription]] = {}
        self.ticks_received = 0

    def latest(self, instrument_id: InstrumentId) -> Tick | None:
        return self._latest.get(instrument_id)

    def on_tick(self, tick: Tick) -> None:
        """Store the newest tick and tell everyone watching that instrument."""
        self.ticks_received += 1
        self._latest[tick.instrument_id] = tick
        for subscription in self._watchers.get(tick.instrument_id, ()):
            subscription._offer(tick)

    def subscribe(self, instrument_ids: Iterable[InstrumentId] = ()) -> Subscription:
        subscription = Subscription(self, ())
        subscription.add(instrument_ids)
        return subscription

    def watched(self) -> set[InstrumentId]:
        """Every instrument at least one subscriber is watching (what feeds must stream)."""
        return {i for i, subs in self._watchers.items() if subs}

    def _index(self, subscription: Subscription, ids: Iterable[InstrumentId]) -> None:
        for instrument_id in ids:
            self._watchers.setdefault(instrument_id, set()).add(subscription)

    def _unindex(self, subscription: Subscription, ids: Iterable[InstrumentId]) -> None:
        for instrument_id in ids:
            watchers = self._watchers.get(instrument_id)
            if watchers is not None:
                watchers.discard(subscription)
                if not watchers:
                    del self._watchers[instrument_id]
