"""The market data cache and its subscriptions.

Prices are state, not history: for each instrument we keep only the newest tick.
Subscribers receive changes through their own Subscription, in one of two ways (#267):

    conflated (screens, P&L)     a slow reader gets only the newest tick per instrument:
                                 three ticks before it reads → one ("newest wins")
    every tick (algos, the       every tick, in arrival order, so a spike through a stop
    paper broker)                that retraces before the next read is still seen. Bounded:
                                 past `limit` unread ticks the oldest are dropped and
                                 counted (`dropped`), and the ops band says so (overload)

Freshness (#267), per instrument, so a busy instrument can't hide a quiet one:
- `age_s(id)`: seconds since its last accepted tick (our clock). The risk gate refuses
  orders that add risk on a price older than its limit; closing orders still go.
- A late tick (its exchange time older than the one we hold) never replaces newer state:
  it is counted in `late_ticks` and dropped.
Fast and slow subscribers never slow each other down.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable

from laddrix.domain.instruments import InstrumentId
from laddrix.domain.ticks import Tick
from laddrix.marketdata.vap import VolumeAtPrice

log = logging.getLogger(__name__)


class Subscription:
    """One subscriber's view: the instruments it watches and the ticks it hasn't read yet."""

    def __init__(
        self,
        service: "MarketData",
        instrument_ids: Iterable[InstrumentId],
        *,
        conflate: bool = True,
        limit: int = 10_000,
    ) -> None:
        self._service = service
        self.instrument_ids: set[InstrumentId] = set(instrument_ids)
        self.conflate = conflate
        self.limit = limit  # every-tick mode: at most this many unread ticks
        self.dropped = 0  # every-tick mode: ticks dropped because the reader fell behind
        self._pending: dict[InstrumentId, Tick] = {}
        self._queue: deque[Tick] = deque()
        self.pending_since_ns = 0  # perf_counter_ns when the oldest unread tick arrived
        self._ready = asyncio.Event()
        self.closed = False

    def _offer(self, tick: Tick) -> None:
        if not self._has_unread():
            self.pending_since_ns = time.perf_counter_ns()
        if self.conflate:
            self._pending[tick.instrument_id] = tick  # replaces any unread older tick
        else:
            if len(self._queue) >= self.limit:
                self._queue.popleft()  # bounded: the oldest unread tick goes, counted
                self.dropped += 1
                self._service.dropped += 1
            self._queue.append(tick)
        self._ready.set()

    def _has_unread(self) -> bool:
        return bool(self._pending) or bool(self._queue)

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
        if ids and self._queue:
            self._queue = deque(t for t in self._queue if t.instrument_id not in ids)

    def drain(self) -> list[Tick]:
        """Take every unread tick now, without waiting: the newest per instrument
        (conflated), or every one in arrival order (every-tick mode)."""
        if self.conflate:
            ticks = list(self._pending.values())
            self._pending.clear()
        else:
            ticks = list(self._queue)
            self._queue.clear()
        self._ready.clear()
        return ticks

    async def wait(self) -> None:
        """Wait until there is something unread (without taking it), or the subscription closes."""
        while not self._has_unread() and not self.closed:
            self._ready.clear()
            await self._ready.wait()

    async def next_batch(self) -> list[Tick]:
        """Wait until something changes, then take every unread tick."""
        while not self._has_unread():
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
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock  # seconds, only differences matter (ages)
        self._seen_at: dict[InstrumentId, float] = {}  # when each instrument's tick came
        self.late_ticks = 0  # ticks older than the state we hold: never replace it
        self.dropped = 0  # every-tick subscribers fell behind: ticks they never saw
        self._latest: dict[InstrumentId, Tick] = {}
        self._watchers: dict[InstrumentId, set[Subscription]] = {}
        self.ticks_received = 0
        self._on_demand: list[Callable[[InstrumentId], None]] = []
        self.vap = VolumeAtPrice()  # today's volume at each price (the ladder's VAP column)
        self._taps: list[Callable[[Tick], None]] = []  # see every tick (e.g. the recorder)

    def latest(self, instrument_id: InstrumentId) -> Tick | None:
        return self._latest.get(instrument_id)

    def all_latest(self) -> list[Tick]:
        """The newest tick of every instrument with a price (movers rank these)."""
        return list(self._latest.values())

    def age_s(self, instrument_id: InstrumentId) -> float | None:
        """Seconds since this instrument's last tick (None: no price yet)."""
        seen = self._seen_at.get(instrument_id)
        return None if seen is None else self._clock() - seen

    def on_tick(self, tick: Tick) -> None:
        """Store the newest tick and tell everyone watching that instrument. A tick older
        (by exchange time) than the one held is late: counted, never applied (#267)."""
        self.ticks_received += 1
        held = self._latest.get(tick.instrument_id)
        then = held.exchange_time if held is not None else None
        if then is not None and tick.exchange_time is not None and tick.exchange_time < then:
            self.late_ticks += 1
            return
        self._latest[tick.instrument_id] = tick
        self._seen_at[tick.instrument_id] = self._clock()
        self.vap.on_tick(tick)
        for subscription in self._watchers.get(tick.instrument_id, ()):
            subscription._offer(tick)
        for tap in self._taps:
            tap(tick)

    def on_derived(self, tick: Tick) -> None:
        """A price made here from other prices (a spread's implied price, spreads/pricer.py).
        Kept and sent to its watchers like any tick, but never to the taps: the recorder,
        paper fills, charts, time and sales, movers and alerts see only the market's own
        prices (recording a derived price would replay it twice: once itself, once from
        its legs). Not counted in `ticks_received`."""
        self._latest[tick.instrument_id] = tick
        self._seen_at[tick.instrument_id] = self._clock()
        for subscription in self._watchers.get(tick.instrument_id, ()):
            subscription._offer(tick)

    def tap(self, listener: Callable[[Tick], None]) -> None:
        """`listener(tick)` for every tick received, watched or not. It must be quick (an
        append): it runs on the trading loop. The tick recorder uses it (research/)."""
        self._taps.append(listener)

    def subscribe(
        self, instrument_ids: Iterable[InstrumentId] = (), *, every_tick: bool = False
    ) -> Subscription:
        """Conflated by default (screens); `every_tick` for what acts on prices (#267)."""
        subscription = Subscription(self, (), conflate=not every_tick)
        subscription.add(instrument_ids)
        return subscription

    def is_watched(self, instrument_id: InstrumentId) -> bool:
        """Does any subscriber watch it? (a dict lookup: safe to ask on every tick)"""
        return bool(self._watchers.get(instrument_id))

    def watched(self) -> set[InstrumentId]:
        """Every instrument at least one subscriber is watching (what feeds must stream)."""
        return {i for i, subs in self._watchers.items() if subs}

    def on_demand(self, callback: Callable[[InstrumentId], None]) -> None:
        """`callback(id)` when an instrument gets its first watcher: a feed subscribes to it
        at once (no polling delay before the first price)."""
        self._on_demand.append(callback)

    def _index(self, subscription: Subscription, ids: Iterable[InstrumentId]) -> None:
        for instrument_id in ids:
            watchers = self._watchers.setdefault(instrument_id, set())
            first = not watchers
            watchers.add(subscription)
            if first:
                for callback in self._on_demand:
                    try:
                        callback(instrument_id)
                    except Exception:
                        log.exception("on_demand callback failed for %s", instrument_id)

    def _unindex(self, subscription: Subscription, ids: Iterable[InstrumentId]) -> None:
        for instrument_id in ids:
            watchers = self._watchers.get(instrument_id)
            if watchers is not None:
                watchers.discard(subscription)
                if not watchers:
                    del self._watchers[instrument_id]
