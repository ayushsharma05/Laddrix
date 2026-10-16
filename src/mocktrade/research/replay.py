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
folder (`tools/demo.py --replay`), so practice never mixes with live or live-paper results.

Instruments come from the day itself (V1.1, #327): the recording keeps what each instrument
was that day (lot, tick, expiry), so a real broker's day, with contracts that have since
expired, replays and trades on paper with no broker login and no live instrument list.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.research.ticks import FileTickStore, NoInstrumentList

log = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SPEEDS = (0, 1, 10, 60)  # 0: as fast as possible
TURNS = 3  # event-loop turns between ticks at full speed: an order placed on one tick is
# accepted (a task) before the next, as it always is live, where ticks are milliseconds apart
MAX_WAIT_S = 5.0  # a long quiet gap (e.g. lunch) never stalls the replay longer than this


class ReplayInstrumentsError(ValueError):
    """The day can't be replayed for trading: what's missing, and what to do about it."""


def replay_instruments(
    store: FileTickStore, day: date, fallback: Iterable[Instrument] = ()
) -> list[Instrument]:
    """Every instrument recorded on `day`, as it was that day.

    The day's own list first; an instrument it doesn't describe may come from `fallback` (the
    demo's simulated market, whose days were recorded before lists were kept). Anything still
    undescribed is refused, naming it: replaying its prices would work, but every order on it
    would fail with "unknown instrument", which is worse than saying so before starting."""
    try:
        saved = store.instruments(day)
    except NoInstrumentList:
        saved = []
    except ValueError as exc:
        raise ReplayInstrumentsError(
            f"{exc}. Delete or fix that file; without it the day can only be replayed if "
            f"every instrument is one of the demo's own."
        ) from None
    found = {i.id: i for i in saved}
    others = {i.id: i for i in fallback}
    recorded = sorted(store.summary(day)["instruments"])
    missing = []
    for instrument_id in recorded:
        if instrument_id not in found:
            if instrument_id in others:
                found[instrument_id] = others[instrument_id]
            else:
                missing.append(instrument_id)
    if missing:
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        shown = ", ".join(missing[:5]) + more
        raise ReplayInstrumentsError(
            f"{day}: {len(missing)} recorded instrument(s) have no details (lot, tick, expiry): "
            f"{shown}. Days recorded before MockTrade kept each day's instrument list can only "
            f"be replayed for the demo's own instruments; days recorded from now on keep it."
        )
    return [found[i] for i in recorded]


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
            seeking = self._seek_to is not None and at <= self._seek_to
            if self._seek_to is not None and not seeking:
                self._seek_to = None
                self._tell()
            if previous is not None and not seeking:
                gap = (recorded_us - previous) / 1_000_000
                if self.speed > 0 and gap > 0:
                    await self._sleep(min(gap / self.speed, MAX_WAIT_S))
                else:
                    for _ in range(TURNS):  # let acceptances and fills run between ticks
                        await self._sleep(0)
            elif seeking and self.fed % 500 == 0:
                await self._sleep(0)  # stay responsive while jumping
            previous = recorded_us
            await self._running.wait()  # paused while waiting: stop before this tick, not after
            self.clock.now = at
            self._md.on_tick(tick)
            self.fed += 1
        self._seek_to = None  # a jump past the end ends with the day
        self.state = "finished"
        self._tell()

    def _tell(self) -> None:
        status = self.status()
        for listener in list(self._listeners):
            try:
                listener(status)
            except Exception:
                log.exception("replay listener failed")
