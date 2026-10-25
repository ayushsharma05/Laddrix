"""Charts: candles for a screen (history joined to live) and the account's trades as marks.

    history (broker / recordings) ──┐
                                    ├─► one list, each bucket once:
    live builder (1-minute, added up)┘   a bucket the builder saw whole comes from the builder
                                         (it is fresher); earlier ones from history; a bucket
                                         only one side has comes from that side

The screen gets the intervals and range limits from `spec()`; nothing about them is coded in it.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mocktrade.charts.candles import (
    BY_CODE,
    INTERVALS,
    CandleBuilder,
    Interval,
    bucket_start,
    exchange_of,
)
from mocktrade.charts.history import CandleHistory, HistoryError
from mocktrade.domain.instruments import InstrumentId
from mocktrade.reports.daily import strategy_of
from mocktrade.store.events import EventLog


class ChartError(Exception):
    """A request that can't be answered (said in words)."""


class Charts:
    def __init__(
        self,
        builder: CandleBuilder,
        history: CandleHistory | None,
        *,
        events: EventLog | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        history_name: str = "",
    ) -> None:
        self.builder = builder
        self.history = history
        self._events = events
        self._clock = clock
        self.history_name = history_name

    def spec(self) -> dict[str, Any]:
        return {
            "intervals": [
                {
                    "code": i.code,
                    "label": i.label,
                    "seconds": i.seconds,
                    "max_range_days": i.max_range_days,
                    "default_days": i.default_days,
                }
                for i in INTERVALS
            ],
            "history": self.history_name or None,  # where past candles come from, if anywhere
            "stream_every_s": 1,
        }

    def interval(self, code: str) -> Interval:
        found = BY_CODE.get(code)
        if found is None:
            raise ChartError(f"interval must be one of {', '.join(BY_CODE)}")
        return found

    async def candles(
        self,
        instrument_id: InstrumentId,
        code: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        interval = self.interval(code)
        end = end or self._clock() + timedelta(seconds=interval.seconds)  # the forming one too
        start = start or end - timedelta(days=interval.default_days)
        if end <= start:
            raise ChartError("the range must end after it starts")
        if (end - start).days > interval.max_range_days:
            raise ChartError(f"at most {interval.max_range_days} days of {interval.label} candles "
                             "in one request")  # fmt: skip
        exchange = exchange_of(instrument_id)
        live = self.builder.candles(instrument_id, interval, start, end)
        complete = self.builder.complete_from(instrument_id)
        # the first bucket the builder saw from its start: from there on, live wins
        whole_from = None
        if complete is not None:
            whole_from = bucket_start(complete, interval, exchange)
            if whole_from < complete:
                whole_from += timedelta(seconds=interval.seconds)
        past: list = []
        note = None
        if self.history is not None:
            try:
                past = await self.history.fetch(instrument_id, interval, start, end)
            except HistoryError as exc:
                note = str(exc)
        merged = {c.start: c for c in past}
        for c in live:
            if (whole_from is not None and c.start >= whole_from) or c.start not in merged:
                merged[c.start] = c
        rows = [merged[k] for k in sorted(merged)]
        return {
            "instrument_id": instrument_id,
            "interval": interval.code,
            "candles": [c.to_dict() for c in rows],
            "live_from": whole_from.isoformat() if whole_from else None,
            "note": note,
        }

    def forming(self, instrument_id: InstrumentId, code: str) -> dict[str, Any] | None:
        candle = self.builder.forming(instrument_id, self.interval(code))
        return candle.to_dict() if candle else None

    def marks(
        self, account_id: str, instrument_id: InstrumentId, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """The account's fills in the instrument, each once (fills can be logged twice)."""
        if self._events is None:
            return []
        out = []
        counted: dict[str, int] = {}
        for e in self._events.fills(account_id):
            order, fill = e.order, e.fill
            if order is None or fill is None or order.request.instrument_id != instrument_id:
                continue
            qty = order.filled_qty - counted.get(e.client_order_id, 0)
            if qty <= 0:
                continue
            counted[e.client_order_id] = order.filled_qty
            if not start <= e.ts < end:
                continue
            r = order.request
            out.append({"t": e.ts.isoformat(), "side": r.side.value, "qty": qty,
                        "price_paise": fill.price_paise, "client_order_id": e.client_order_id,
                        "strategy": strategy_of(r)})  # fmt: skip
        return out


# --- a spread's chart (#429) ---------------------------------------------------------------

SPREAD_NOTE = (
    "spread mark from leg prices (Σ price× · each leg's open and close), not traded "
    "prices; high and low are the open's and close's; gaps where a leg had no data"
)


def combine(legs: list[tuple[int, dict[str, Any] | None]]) -> dict[str, Any] | None:
    """One spread candle from its legs' candles of the same bucket (each with its price×), or
    None if a leg has none. Open and close are the legs' together; a high and low can't be
    known from the legs' own, which may come at different moments, so they are the open's
    and close's. No volume: a spread mark doesn't trade."""
    if any(c is None for _, c in legs):
        return None
    starts = {c["t"] for _, c in legs if c is not None}
    if len(starts) != 1:
        return None
    o = sum(m * c["o"] for m, c in legs if c is not None)
    cl = sum(m * c["c"] for m, c in legs if c is not None)
    return {"t": starts.pop(), "o": o, "h": max(o, cl), "l": min(o, cl), "c": cl, "v": 0,
            "oi": None}  # fmt: skip


async def spread_candles(charts: Charts, spread: Any, code: str, start: datetime | None = None,
                         end: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """A spread's candles from its legs' (each fetched as any chart would): a bucket counts
    only when every leg has a candle in it; one some leg lacks is a gap, never filled in. The
    legs are fetched together: a history source keeps to its own rate limit."""
    per_leg = await asyncio.gather(
        *(charts.candles(leg.instrument_id, code, start, end) for leg in spread.legs)
    )
    by_t = [{c["t"]: c for c in got["candles"]} for got in per_leg]
    every = sorted(set().union(*by_t))
    rows, gaps = [], 0
    for t in every:
        pairs = zip(spread.legs, by_t, strict=True)
        candle = combine([(leg.multiplier, have.get(t)) for leg, have in pairs])
        if candle is None:
            gaps += 1
        else:
            rows.append(candle)
    notes = [n for n in (got.get("note") for got in per_leg) if n]
    lives = [got.get("live_from") for got in per_leg]
    return {
        "instrument_id": spread.instrument_id,
        "interval": per_leg[0]["interval"] if per_leg else code,
        "candles": rows,
        "live_from": max(known) if len(known := [x for x in lives if x]) == len(lives) else None,
        "note": "; ".join(notes) or None,
        "kind": "spread mark",
        "approximate_high_low": True,
        "gaps": gaps,
        "label": SPREAD_NOTE,
    }


def spread_forming(charts: Charts, spread: Any, code: str) -> dict[str, Any] | None:
    """The spread's forming candle: its legs' forming candles, when every leg has one in the
    same bucket."""
    return combine([(leg.multiplier, charts.forming(leg.instrument_id, code))
                    for leg in spread.legs])  # fmt: skip
