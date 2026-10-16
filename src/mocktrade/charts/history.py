"""Past candles: where a chart's history comes from (replaceable).

    KiteHistory      GET /instruments/historical/{token}/{interval}?from=&to=&oi=1
                     ranges split to Kite's per-interval limit, at most `per_second` requests a
                     second, past days kept in memory (they never change)
    RecordedHistory  the recorded ticks (research/ticks.py) built into candles by the same
                     builder as live (paper trading and the demo: no broker history there)

Both answer 1-minute candles or any interval; the chart service joins them to the live ones.
"""

import asyncio
import time as monotonic_time
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

from mocktrade.charts.candles import (
    IST,
    MINUTE,
    Candle,
    CandleBuilder,
    Interval,
    aggregate,
    bucket_start,
    exchange_of,
)
from mocktrade.domain.instruments import InstrumentId
from mocktrade.research.ticks import EPOCH, FileTickStore


class HistoryError(Exception):
    """History can't be had (said in words): the chart still shows the live candles."""


class CandleHistory(Protocol):
    async def fetch(
        self, instrument_id: InstrumentId, interval: Interval, start: datetime, end: datetime
    ) -> list[Candle]: ...


def _days(start: datetime, end: datetime) -> tuple[date, date]:
    """The India days a [start, end) range touches."""
    return start.astimezone(IST).date(), (end - timedelta(microseconds=1)).astimezone(IST).date()


def _paise(value: Any) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value())


class KiteHistory:
    def __init__(
        self,
        http: Any,  # KiteHttp (the prices account's): request("GET", path, params=…)
        token_of: Callable[[InstrumentId], str | None],
        *,
        per_second: float = 3.0,  # Kite's historical limit (forum-reported; check before live)
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Any] = asyncio.sleep,
        monotonic: Callable[[], float] = monotonic_time.monotonic,
    ) -> None:
        self._http = http
        self._token_of = token_of
        self._gap = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self._cache: dict[tuple[str, str, date, date], list[Candle]] = {}
        self.requests = 0

    async def fetch(
        self, instrument_id: InstrumentId, interval: Interval, start: datetime, end: datetime
    ) -> list[Candle]:
        token = self._token_of(instrument_id)
        if token is None:
            raise HistoryError(f"Kite has no history for {instrument_id}")
        out: list[Candle] = []
        today = self._clock().astimezone(IST).date()
        first, last = _days(start, end)
        step = timedelta(days=interval.kite_max_days - 1)
        day = first
        while day <= last:
            to = min(day + step, last)
            key = (instrument_id, interval.code, day, to)
            rows = self._cache.get(key)
            if rows is None:
                rows = await self._get(token, interval, day, to)
                if to < today:  # a finished day never changes
                    self._cache[key] = rows
            out.extend(c for c in rows if start <= c.start < end)
            day = to + timedelta(days=1)
        return out

    async def _get(self, token: str, interval: Interval, first: date, last: date) -> list[Candle]:
        async with self._lock:  # one request at a time, spaced to the rate limit
            wait = self._next_at - self._monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._next_at = self._monotonic() + self._gap
            self.requests += 1
            params = {"from": f"{first.isoformat()} 00:00:00", "to": f"{last.isoformat()} 23:59:59",
                      "oi": 1, "continuous": 0}  # fmt: skip
            try:
                data = await self._http.request(
                    "GET", f"/instruments/historical/{token}/{interval.kite}", params=params
                )
            except Exception as exc:  # KiteError, network: said in words, the live part stays
                raise HistoryError(f"Kite history: {exc}") from exc
        out = []
        for row in (data or {}).get("candles", []):
            at = datetime.fromisoformat(str(row[0])).astimezone(IST)
            oi = int(row[6]) if len(row) > 6 and row[6] is not None else None
            out.append(Candle(at, _paise(row[1]), _paise(row[2]), _paise(row[3]),
                              _paise(row[4]), int(row[5]), oi))  # fmt: skip
        return out


class RecordedHistory:
    def __init__(
        self, ticks: FileTickStore, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._ticks = ticks
        self._clock = clock
        self._cache: dict[tuple[InstrumentId, date], list[Candle]] = {}

    async def fetch(
        self, instrument_id: InstrumentId, interval: Interval, start: datetime, end: datetime
    ) -> list[Candle]:
        today = self._clock().astimezone(IST).date()
        first, last = _days(start, end)
        minutes: list[Candle] = []
        for day in self._ticks.days():
            if first <= day <= last:
                key = (instrument_id, day)
                day_minutes = self._cache.get(key)
                if day_minutes is None:
                    day_minutes = await asyncio.to_thread(self._build, instrument_id, day)
                    if day < today:
                        self._cache[key] = day_minutes
                minutes.extend(day_minutes)
        exchange = exchange_of(instrument_id)
        window = bucket_start(start, interval, exchange)
        built = aggregate((m for m in minutes if window <= m.start < end), interval, exchange)
        return [c for c in built if start <= c.start < end]

    def _build(self, instrument_id: InstrumentId, day: date) -> list[Candle]:
        builder = CandleBuilder(keep_minutes=24 * 60)
        for recorded_us, tick in self._ticks.read_timed(day, [instrument_id]):
            if tick.exchange_time is None and tick.received_at is None:
                tick = tick._replace(received_at=EPOCH + timedelta(microseconds=recorded_us))
            builder.on_tick(tick)
        opens = datetime.combine(day, time(0), IST)
        return builder.candles(instrument_id, MINUTE, opens, opens + timedelta(days=1))
