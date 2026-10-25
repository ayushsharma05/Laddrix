"""Recorded ticks: every tick MockTrade receives, kept for replay (K11.2) and backtests (K11.3).

    MarketData.tap ─► TickRecorder.on_tick (an append: the trading loop never waits)
                   ─► every `flush_s`, in a worker thread ─► TickStore.append(batch)
    FileTickStore:  <root>/<YYYY-MM-DD>/<segment>.ticks.gz   one file per India day and segment
                    <root>/<YYYY-MM-DD>/index.json           tick counts per instrument
                    <root>/<YYYY-MM-DD>/instruments.json     what each recorded instrument was
                                                             that day (V1.1, #327)

The instrument list makes a day replayable on its own: a contract that has since expired, or
a day recorded on another server, still has its lot, tick and expiry. An instrument's details
are written before its first ticks, so a crash never leaves ticks nobody can describe.

Each flush appends one gzip member (gzip files may hold many, read back as one stream), so a
crash loses at most the last unflushed second, and a member cut short by a crash is skipped on
reading (everything before it is kept). Lines are compact JSON arrays: every Tick field, times
as epoch microseconds; a tick read back equals the tick written, to the field.

Older days than `keep_days` are deleted by `rotate()` (the scheduler runs it daily); the disk
check (ops/checks.py) warns before the disk fills. Measured on the demo's simulated market:
see docs/design/research.md.
"""

import asyncio
import gzip
import heapq
import json
import logging
import os
import shutil
import zlib
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.refdata.store import instrument_from_record, instrument_record

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SUFFIX = ".ticks.gz"
INSTRUMENTS = "instruments.json"


class NoInstrumentList(LookupError):
    """A day recorded before recordings kept their instrument list (or the list was deleted)."""


class TickStore(Protocol):
    """Replaceable: files today; a column store or object storage later."""

    @property
    def keep_days(self) -> int: ...  # how many days are kept (0: recording is off)

    def append(self, batch: list[tuple[Tick, datetime]]) -> None: ...

    def days(self) -> list[date]: ...

    def summary(self, day: date) -> dict[str, Any]: ...

    def read(self, day: date, instruments: Iterable[str] | None = None) -> Iterator[Tick]: ...

    def today(self) -> date: ...

    def delete(self, day: date) -> None: ...

    def instruments(self, day: date) -> list[Instrument]: ...


# --- one tick as one line ------------------------------------------------------------------------


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICRO = timedelta(microseconds=1)


def _us(at: datetime | None) -> int | None:
    """Exact epoch microseconds (no floats). A time without a zone is taken as UTC."""
    if at is None:
        return None
    aware = at if at.tzinfo else at.replace(tzinfo=UTC)
    return (aware - EPOCH) // MICRO


def _at(us: int | None) -> datetime | None:
    return None if us is None else EPOCH + timedelta(microseconds=us)


def encode(tick: Tick, recorded_at: datetime) -> str:
    row = [
        tick.instrument_id, tick.ltp_paise, _us(tick.received_at), tick.last_qty,
        tick.avg_price_paise, tick.volume, tick.buy_qty, tick.sell_qty, tick.open_paise,
        tick.high_paise, tick.low_paise, tick.close_paise, tick.open_interest,
        _us(tick.last_trade_time), _us(tick.exchange_time),
        [list(level) for level in tick.bids], [list(level) for level in tick.asks],
        _us(recorded_at),
    ]  # fmt: skip
    return json.dumps(row, separators=(",", ":"))


def decode(line: str) -> tuple[Tick, int]:
    """The tick and when it was recorded (epoch microseconds, for merging files in order)."""
    r = json.loads(line)
    tick = Tick(
        r[0], r[1], _at(r[2]), r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12],
        _at(r[13]), _at(r[14]),
        tuple(DepthLevel(*level) for level in r[15]), tuple(DepthLevel(*level) for level in r[16]),
    )  # fmt: skip
    return tick, r[17]


# --- files ---------------------------------------------------------------------------------


class FileTickStore:
    def __init__(
        self,
        root: str | Path,
        *,
        keep_days: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        describe: Callable[[str], Instrument | None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.keep_days = keep_days
        self._clock = clock
        self._describe = describe  # the instrument master's lookup; None: no list is kept
        self._described: dict[date, set[str]] = {}  # per day, instruments already in the list

    def append(self, batch: list[tuple[Tick, datetime]]) -> None:
        """Write one batch (a worker thread calls this, never the trading loop)."""
        groups: dict[tuple[date, str], list[str]] = {}
        counts: dict[date, dict[str, int]] = {}
        for tick, recorded_at in batch:
            day = recorded_at.astimezone(IST).date()
            segment = tick.instrument_id.split(":", 1)[0]
            groups.setdefault((day, segment), []).append(encode(tick, recorded_at))
            per_day = counts.setdefault(day, {})
            per_day[tick.instrument_id] = per_day.get(tick.instrument_id, 0) + 1
        for day, per_instrument in counts.items():  # describe first: no tick goes undescribed
            self._remember(day, per_instrument)
        for (day, segment), lines in groups.items():
            folder = self.root / day.isoformat()
            folder.mkdir(parents=True, exist_ok=True)
            data = gzip.compress(("\n".join(lines) + "\n").encode(), compresslevel=6)
            with open(folder / f"{segment}{SUFFIX}", "ab") as f:  # one gzip member per flush
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        for day, per_instrument in counts.items():
            self._count(day, per_instrument)

    def days(self) -> list[date]:
        found = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                try:
                    found.append(date.fromisoformat(d.name))
                except ValueError:
                    continue
        return sorted(found)

    def summary(self, day: date) -> dict[str, Any]:
        folder = self.root / day.isoformat()
        index = self._index(day)
        files = {f.name[: -len(SUFFIX)]: f.stat().st_size for f in folder.glob(f"*{SUFFIX}")}
        return {
            "day": day.isoformat(),
            "ticks": sum(index.values()),
            "bytes": sum(files.values()),
            "segments": files,
            "instruments": dict(sorted(index.items())),
        }

    def read(self, day: date, instruments: Iterable[str] | None = None) -> Iterator[Tick]:
        """Every tick of the day in the order they were recorded (segments merged)."""
        for _, tick in self.read_timed(day, instruments):
            yield tick

    def read_timed(
        self, day: date, instruments: Iterable[str] | None = None
    ) -> Iterator[tuple[int, Tick]]:
        """(when it was recorded, epoch µs; the tick), in recorded order: what a replay needs."""
        wanted = set(instruments) if instruments is not None else None
        folder = self.root / day.isoformat()
        streams = [self._lines(f, wanted) for f in sorted(folder.glob(f"*{SUFFIX}"))]
        for recorded, _, tick in heapq.merge(*streams):
            yield recorded, tick

    def rotate(self, today: date | None = None) -> list[date]:
        """Delete the oldest days beyond `keep_days`. Returns what was deleted."""
        days = self.days()
        if self.keep_days <= 0 or len(days) <= self.keep_days:
            return []
        gone = days[: len(days) - self.keep_days]
        for day in gone:
            shutil.rmtree(self.root / day.isoformat(), ignore_errors=True)
            log.info("tick recordings of %s deleted (keeping %d days)", day, self.keep_days)
        return gone

    def today(self) -> date:
        """The India day being written now."""
        return self._clock().astimezone(IST).date()

    def delete(self, day: date) -> None:
        """Delete one recorded day (a person asked). Today's day is still being written: refused."""
        if day == self.today():
            raise ValueError(f"{day} is today: it is still being recorded")
        folder = self.root / day.isoformat()
        if not folder.is_dir():
            raise LookupError(f"nothing recorded on {day}")
        shutil.rmtree(folder)
        log.info("tick recordings of %s deleted on request", day)

    def instruments(self, day: date) -> list[Instrument]:
        """What each instrument recorded that day was (NoInstrumentList if the day has none;
        ValueError, naming the day, if the list is broken)."""
        path = self.root / day.isoformat() / INSTRUMENTS
        try:
            text = path.read_text()
        except FileNotFoundError:
            raise NoInstrumentList(f"{day}: no instrument list ({INSTRUMENTS})") from None
        try:
            return [instrument_from_record(r) for r in json.loads(text).values()]
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"{day}: the instrument list {path} is broken: {exc}") from None

    # --- inside -----------------------------------------------------------------------------

    def _remember(self, day: date, instrument_ids: Iterable[str]) -> None:
        """Add the details of instruments new to this day's list (those the master knows)."""
        if self._describe is None:
            return
        known = self._described.get(day)
        if known is None:
            known = self._described[day] = set(self._saved(day))
        new = {}
        for instrument_id in instrument_ids:
            if instrument_id in known:
                continue
            instrument = self._describe(instrument_id)
            if instrument is not None:  # unknown to the master: nothing to say about it
                new[instrument_id] = instrument_record(instrument)
        if not new:
            return
        records = {**self._saved(day), **new}
        folder = self.root / day.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{INSTRUMENTS}.tmp"
        tmp.write_text(json.dumps(records, sort_keys=True))
        os.replace(tmp, folder / INSTRUMENTS)
        known.update(new)

    def _saved(self, day: date) -> dict[str, Any]:
        try:
            saved = json.loads((self.root / day.isoformat() / INSTRUMENTS).read_text())
        except (OSError, ValueError):
            return {}  # none yet, or broken: rewritten whole from what is described now
        return saved if isinstance(saved, dict) else {}

    def _lines(self, path: Path, wanted: set[str] | None) -> Iterator[tuple[int, int, Tick]]:
        n = 0
        try:
            with gzip.open(path, "rt") as f:
                for line in f:
                    if not line.endswith("\n"):
                        break  # cut short by a crash
                    tick, recorded = decode(line)
                    n += 1
                    if wanted is None or tick.instrument_id in wanted:
                        yield recorded, n, tick
        except (EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:
            log.warning("%s: the end was cut short (%s); %d ticks read", path, exc, n)

    def _index(self, day: date) -> dict[str, int]:
        path = self.root / day.isoformat() / "index.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _count(self, day: date, more: dict[str, int]) -> None:
        index = self._index(day)
        for instrument_id, n in more.items():
            index[instrument_id] = index.get(instrument_id, 0) + n
        path = self.root / day.isoformat() / "index.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, sort_keys=True))
        os.replace(tmp, path)


# --- the recorder ---------------------------------------------------------------------------


class TickRecorder:
    def __init__(
        self,
        store: TickStore,
        *,
        flush_s: float = 1.0,
        record: Callable[[str], bool] = lambda _instrument_id: True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.flush_s = flush_s
        self._record = record
        self._clock = clock
        self._buffer: list[tuple[Tick, datetime]] = []
        self.recorded = 0
        self.failures = 0

    def on_tick(self, tick: Tick) -> None:
        """MarketData tap: one append, nothing else, on the trading loop."""
        if self._record(tick.instrument_id):
            self._buffer.append((tick, self._clock()))

    async def flush(self) -> None:
        batch, self._buffer = self._buffer, []
        if not batch:
            return
        try:
            await asyncio.to_thread(self.store.append, batch)
            self.recorded += len(batch)
        except Exception:
            self.failures += 1
            log.exception("tick recording: %d ticks not written", len(batch))
            self._buffer = batch + self._buffer  # kept; tried again with the next flush

    async def run(self) -> None:
        """Flush every `flush_s` until cancelled; the last ticks are written on the way out."""
        try:
            while True:
                await asyncio.sleep(self.flush_s)
                await self.flush()
        finally:
            await asyncio.shield(self.flush())
