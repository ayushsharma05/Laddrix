"""Replay a recorded day (K11.1) into MarketData, so paper trading runs on a real past day.

    FileTickStore.read_timed(day) ─► ReplayFeed ─► clock.now = the tick's recorded time
                                               ─► MarketData.on_tick(tick)
                                               ─► waits (gap between ticks) / speed

Everything downstream (the paper broker, algos, positions, MTM, square-off, reports) is the
normal code, given `clock` so it sees the replayed time, not the wall clock: a TWAP schedule,
the 15:00 square-off window and the end-of-day report all follow the day being replayed.

Controls: pause, resume, speed (1×, 10×, 60×, 0 = as fast as possible), and jump ahead to a
time (the ticks in between are fed at once, so prices, VAP and resting orders stay consistent;
going back means starting the replay again). A replay runs in its own server with its own data
folder (tools/replay.py), so practice never mixes with live or live-paper results.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData

log = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SPEEDS = (0, 1, 10, 60)  # 0: as fast as possible
MAX_WAIT_S = 5.0  # a long quiet gap (e.g. lunch) never stalls the replay longer than this


class ReplayClock:
    """The replayed time; every component's `clock` in a replay server."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class ReplayFeed:
    def __init__(
        self,
        ticks: Callable[[], Iterator[tuple[int, Tick]]],  # (recorded epoch µs, tick), in order
        market_data: MarketData,
        clock: ReplayClock,
        *,
        day: date,
        speed: float = 10,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._ticks = ticks
        self._md = market_data
        self.clock = clock
        self.day = day
        self.speed = speed
        self._sleep = sleep
        self._running = asyncio.Event()
        self._running.set()
        self.state = "ready"  # ready | playing | paused | finished
        self.fed = 0
        self._seek_to: datetime | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # --- controls ------------------------------------------------------------------------------

    def pause(self) -> None:
        if self.state == "playing":
            self._running.clear()
            self.state = "paused"
            self._tell()

    def resume(self) -> None:
        if self.state == "paused":
            self._running.set()
            self.state = "playing"
            self._tell()

    def set_speed(self, speed: float) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {', '.join(str(s) for s in SPEEDS)} (0: max)")
        self.speed = speed
        self._tell()

    def seek(self, to: datetime) -> None:
        """Jump ahead: the ticks before `to` are fed at once (never backwards)."""
        if to <= self.clock.now:
            raise ValueError("a replay only jumps ahead: start it again to go back")
        self._seek_to = to
        self._tell()

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def status(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "state": self.state,
            "speed": self.speed,
            "at": self.clock.now.isoformat(),
            "fed": self.fed,
            "seeking_to": self._seek_to.isoformat() if self._seek_to else None,
        }

    # --- running -------------------------------------------------------------------------------

    async def run(self) -> None:
        self.state = "playing"
        self._tell()
        previous: int | None = None
        for recorded_us, tick in self._ticks():
            await self._running.wait()
            at = EPOCH + timedelta(microseconds=recorded_us)
            seeking = self._seek_to is not None and at < self._seek_to
            if self._seek_to is not None and not seeking:
                self._seek_to = None
                self._tell()
            if previous is not None and not seeking:
                gap = (recorded_us - previous) / 1_000_000
                if self.speed > 0 and gap > 0:
                    await self._sleep(min(gap / self.speed, MAX_WAIT_S))
                else:
                    await self._sleep(0)  # let fills and screens run between ticks
            elif seeking and self.fed % 500 == 0:
                await self._sleep(0)  # stay responsive while jumping
            previous = recorded_us
            self.clock.now = at
            self._md.on_tick(tick)
            self.fed += 1
        self.state = "finished"
        self._tell()

    def _tell(self) -> None:
        status = self.status()
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:
                log.exception("replay listener failed")
