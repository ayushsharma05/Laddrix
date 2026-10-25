"""Candles (OHLCV): built live from ticks, one level only (1 minute), added up for the rest.

    MarketData.tap ─► CandleBuilder.on_tick ─► the instrument's 1-minute candles (one update)
    a chart asks for 15m ─► the 1-minute candles in range, added up into 15-minute buckets

Buckets are India time, aligned to the session open (09:15 for NSE/BSE and their F&O): the
15-minute candles are 09:15, 09:30…, the hourly ones 09:15, 10:15… (as Kite's are); a day
candle is the India date.

Volume comes from the change in the day's total volume (`tick.volume`), never `last_qty`,
which a snapshot feed repeats: the same rule as volume at price (marketdata/vap.py). The first
tick seen is a baseline (what traded before it is unknown); a total that goes down is a new
day. A tick with no new volume and the same price is a quote, not a trade: no candle from it.

The first minute the builder sees is partial (it started mid-minute), so the builder says from
when its candles are complete; the chart service (charts/service.py) takes earlier candles
from the broker's history instead, so nothing is missed or counted twice.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from laddrix.domain.instruments import InstrumentId
from laddrix.domain.ticks import Tick

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Interval:
    code: str  # what the API and screens use
    label: str
    seconds: int
    kite: str  # Kite's name for it (historical API)
    kite_max_days: int  # Kite's longest range per request (forum-reported; check before live)
    max_range_days: int  # the longest range one chart request may ask for (keeps answers small)
    default_days: int  # what a chart shows first, unless it asks for a range


INTERVALS: tuple[Interval, ...] = (
    Interval("1m", "1 minute", 60, "minute", 60, 5, 1),
    Interval("3m", "3 minutes", 180, "3minute", 100, 15, 2),
    Interval("5m", "5 minutes", 300, "5minute", 100, 30, 3),
    Interval("15m", "15 minutes", 900, "15minute", 200, 60, 10),
    Interval("30m", "30 minutes", 1800, "30minute", 200, 120, 20),
    Interval("1h", "1 hour", 3600, "60minute", 400, 240, 30),
    Interval("1d", "1 day", 86400, "day", 2000, 3650, 365),
)
BY_CODE = {i.code: i for i in INTERVALS}
MINUTE = BY_CODE["1m"]
DAY = 86400

# When each exchange's session opens (India time): intraday buckets are aligned to it.
SESSION_OPEN: dict[str, time] = {"NSE": time(9, 15), "BSE": time(9, 15), "NFO": time(9, 15),
                                 "BFO": time(9, 15)}  # fmt: skip
DEFAULT_OPEN = time(9, 15)


def exchange_of(instrument_id: InstrumentId) -> str:
    return instrument_id.split(":", 1)[0]


def bucket_start(at: datetime, interval: Interval, exchange: str) -> datetime:
    """The start (India time) of the candle `at` falls in."""
    local = (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(IST)
    if interval.seconds >= DAY:
        return datetime.combine(local.date(), time(0), IST)
    opens = datetime.combine(local.date(), SESSION_OPEN.get(exchange, DEFAULT_OPEN), IST)
    n = (local - opens) // timedelta(seconds=interval.seconds)  # floor, also before the open
    return opens + n * timedelta(seconds=interval.seconds)


@dataclass(slots=True)
class Candle:
    start: datetime  # India time
    open: int  # paise
    high: int
    low: int
    close: int
    volume: int
    oi: int | None = None  # open interest at the close of the candle

    def to_dict(self) -> dict[str, Any]:
        return {"t": self.start.isoformat(), "o": self.open, "h": self.high, "l": self.low,
                "c": self.close, "v": self.volume, "oi": self.oi}  # fmt: skip


def aggregate(minutes: Iterable[Candle], interval: Interval, exchange: str) -> list[Candle]:
    """Smaller candles (in time order) added up into `interval` buckets."""
    out: list[Candle] = []
    for m in minutes:
        start = bucket_start(m.start, interval, exchange)
        last = out[-1] if out else None
        if last is not None and last.start == start:
            last.high = max(last.high, m.high)
            last.low = min(last.low, m.low)
            last.close = m.close
            last.volume += m.volume
            if m.oi is not None:
                last.oi = m.oi
        else:
            out.append(Candle(start, m.open, m.high, m.low, m.close, m.volume, m.oi))
    return out


@dataclass
class _Seen:
    minutes: list[Candle]
    since: datetime  # the first tick's time: candles are complete from the next minute
    volume: int | None = None  # the day's total volume at the last tick counted
    ltp: int | None = None


class CandleBuilder:
    def __init__(
        self,
        *,
        keep_minutes: int = 1500,  # about four trading days of 1-minute candles
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.keep_minutes = keep_minutes
        self._clock = clock
        self._seen: dict[InstrumentId, _Seen] = {}
        self.out_of_order = 0  # ticks older than the instrument's newest candle (ignored)

    def on_tick(self, tick: Tick) -> None:
        """MarketData tap: a dict lookup and one candle update on the trading loop."""
        at = tick.exchange_time or tick.received_at or self._clock()
        seen = self._seen.get(tick.instrument_id)
        traded = 0
        if seen is not None and tick.volume is not None and seen.volume is not None:
            if tick.volume >= seen.volume:
                traded = tick.volume - seen.volume
            if traded == 0 and tick.ltp_paise == seen.ltp and tick.volume >= seen.volume:
                seen.volume = tick.volume
                return  # a quote, not a trade
        start = bucket_start(at, MINUTE, exchange_of(tick.instrument_id))
        if seen is None:
            seen = self._seen[tick.instrument_id] = _Seen([], at)
        seen.volume = tick.volume if tick.volume is not None else seen.volume
        seen.ltp = tick.ltp_paise
        last = seen.minutes[-1] if seen.minutes else None
        price = tick.ltp_paise
        if last is not None and start == last.start:
            last.high = max(last.high, price)
            last.low = min(last.low, price)
            last.close = price
            last.volume += traded
            if tick.open_interest is not None:
                last.oi = tick.open_interest
        elif last is not None and start < last.start:
            self.out_of_order += 1  # a late tick: its minute is already closed
        else:
            seen.minutes.append(Candle(start, price, price, price, price, traded,
                                       tick.open_interest))  # fmt: skip
            if len(seen.minutes) > self.keep_minutes:
                del seen.minutes[: len(seen.minutes) - self.keep_minutes]

    def complete_from(self, instrument_id: InstrumentId) -> datetime | None:
        """From when this builder's candles are whole (the minute after its first tick)."""
        seen = self._seen.get(instrument_id)
        if seen is None:
            return None
        return bucket_start(seen.since, MINUTE, exchange_of(instrument_id)) + timedelta(minutes=1)

    def candles(
        self, instrument_id: InstrumentId, interval: Interval, start: datetime, end: datetime
    ) -> list[Candle]:
        """Candles whose bucket starts in [start, end), built from the live 1-minute ones."""
        seen = self._seen.get(instrument_id)
        if seen is None:
            return []
        exchange = exchange_of(instrument_id)
        first = bucket_start(start, interval, exchange)
        minutes = [m for m in seen.minutes if first <= m.start < end]
        return [c for c in aggregate(minutes, interval, exchange) if start <= c.start < end]

    def forming(self, instrument_id: InstrumentId, interval: Interval) -> Candle | None:
        """The newest candle of this interval (the one still forming, during the session)."""
        seen = self._seen.get(instrument_id)
        if seen is None or not seen.minutes:
            return None
        exchange = exchange_of(instrument_id)
        minutes = seen.minutes
        start = bucket_start(minutes[-1].start, interval, exchange)
        i = len(minutes)
        while i > 0 and minutes[i - 1].start >= start:
            i -= 1
        return aggregate(minutes[i:], interval, exchange)[-1]
