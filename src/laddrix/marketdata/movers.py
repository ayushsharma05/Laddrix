"""Movers: the biggest gainers, losers, volume and open-interest change among the instruments
this server has prices for. Worked out by the server from the latest ticks (no screen ranks).

    gainers / losers   % change of the last price from the previous close
    volume             the day's traded quantity
    oi                 the biggest % change of open interest either way, since the first OI
                       seen today (Kite's ticks
                       carry no previous-day OI, so the screen says "since first seen today")

Filters: exchange (NSE, NFO…) and kind (EQ, FUT, CE, PE, INDEX).
"""

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from laddrix.domain.instruments import Instrument, InstrumentId
from laddrix.domain.ticks import Tick

IST = ZoneInfo("Asia/Kolkata")
BY = ("gainers", "losers", "volume", "oi")
MAX_LIMIT = 50


class MoversError(Exception):
    """A request that can't be answered (said in words)."""


class Movers:
    def __init__(
        self,
        latest: Callable[[], Iterable[Tick]],
        instrument: Callable[[InstrumentId], Instrument | None],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._latest = latest
        self._instrument = instrument
        self._clock = clock
        self._oi_base: dict[InstrumentId, tuple[date, int]] = {}

    def on_tick(self, tick: Tick) -> None:
        """MarketData tap: remember each instrument's first open interest of the day."""
        if tick.open_interest is None:
            return
        today = self._clock().astimezone(IST).date()
        kept = self._oi_base.get(tick.instrument_id)
        if kept is None or kept[0] != today:
            self._oi_base[tick.instrument_id] = (today, tick.open_interest)

    def top(
        self, by: str, *, limit: int = 10, exchange: str = "", kind: str = ""
    ) -> dict[str, Any]:
        if by not in BY:
            raise MoversError(f"by must be one of {', '.join(BY)}")
        if not 1 <= limit <= MAX_LIMIT:
            raise MoversError(f"limit must be 1–{MAX_LIMIT}")
        today = self._clock().astimezone(IST).date()
        rows: list[dict[str, Any]] = []
        exchanges: set[str] = set()
        kinds: set[str] = set()
        for t in self._latest():
            i = self._instrument(t.instrument_id)
            if i is None:
                continue
            exchanges.add(i.exchange.value)
            kinds.add(i.kind.value)
            if exchange and i.exchange.value != exchange:
                continue
            if kind and i.kind.value != kind:
                continue
            change = None
            if t.close_paise:
                change = round((t.ltp_paise - t.close_paise) * 100 / t.close_paise, 2)
            oi_change = None
            base = self._oi_base.get(t.instrument_id)
            if t.open_interest is not None and base and base[0] == today and base[1] > 0:
                oi_change = round((t.open_interest - base[1]) * 100 / base[1], 2)
            rows.append({"instrument_id": t.instrument_id, "symbol": i.symbol,
                         "ltp_paise": t.ltp_paise, "change_pct": change, "volume": t.volume,
                         "oi": t.open_interest, "oi_change_pct": oi_change})  # fmt: skip
        key = {"gainers": "change_pct", "losers": "change_pct", "volume": "volume",
               "oi": "oi_change_pct"}[by]  # fmt: skip
        have = [r for r in rows if r[key] is not None]
        if by == "gainers":
            have = [r for r in have if r[key] > 0]
        elif by == "losers":
            have = [r for r in have if r[key] < 0]
        elif by == "oi":
            have = [r for r in have if r[key] != 0]
        # biggest first (losers: most negative first; OI: the biggest move either way); ties by
        # instrument, so the order is stable
        if by == "oi":
            have.sort(key=lambda r: (-abs(r[key]), r["instrument_id"]))
        else:
            sign = 1 if by == "losers" else -1
            have.sort(key=lambda r: (sign * r[key], r["instrument_id"]))
        note = "OI change since the first open interest seen today" if by == "oi" else ""
        return {
            "by": by,
            "rows": have[:limit],
            "of": len(rows),
            "note": note,
            # what was ranked (#277): only contracts with a live price here, never the exchange
            "universe": f"ranked among the {len(rows)} contracts with a live price on this "
            "server (those a screen, an order or an algo watches), not the whole "
            "exchange",  # fmt: skip
            "exchanges": sorted(exchanges),
            "kinds": sorted(kinds),
        }  # what can be filtered
