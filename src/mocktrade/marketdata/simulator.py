"""A market simulator: realistic ticks without Kite, for the demo server and UI work.

Each instrument does a random walk on its tick grid, gently pulled back to where it
started, and publishes ticks shaped like Kite's full mode: last price and quantity,
5 levels of depth each side, cumulative volume, open/high/low and the previous close.
About one tick per instrument per second, like Kite. Seeded, so tests are repeatable.

It never trades with you: the paper broker fills against these prices exactly as it
would against Kite's.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mocktrade.domain.instruments import Instrument, InstrumentId
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData


@dataclass
class _Walk:
    instrument: Instrument
    anchor: int  # where the price started; the walk is pulled back towards it
    ltp: int
    close: int
    open: int
    high: int
    low: int
    volume: int = 0
    up: bool = True
    depth_seed: list[int] = field(default_factory=list)


class MarketSimulator:
    def __init__(
        self,
        market_data: MarketData,
        start_prices: dict[Instrument, int],  # instrument -> starting price in paise
        *,
        seed: int = 7,
        interval_s: float = 1.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._md = market_data
        self._rng = random.Random(seed)
        self._interval_s = interval_s
        self._clock = clock
        self._sleep = sleep
        self._walks: dict[InstrumentId, _Walk] = {}
        for instrument, price in start_prices.items():
            if price <= 0 or price % instrument.tick_paise:
                raise ValueError(f"{instrument.id}: start price must be on the tick grid")
            close = price - instrument.tick_paise * self._rng.randint(-40, 40)
            close = max(close, instrument.tick_paise)
            self._walks[instrument.id] = _Walk(
                instrument, price, price, close, close, max(price, close), min(price, close)
            )

    @property
    def instruments(self) -> list[InstrumentId]:
        return list(self._walks)

    def step(self) -> list[Tick]:
        """Move every instrument once and publish the ticks. Returns them (for tests)."""
        ticks = [self._move(walk) for walk in self._walks.values()]
        for tick in ticks:
            self._md.on_tick(tick)
        return ticks

    async def run(self) -> None:
        """Publish forever, about one tick per instrument per interval."""
        while True:
            self.step()
            await self._sleep(self._interval_s)

    def _move(self, walk: _Walk) -> Tick:
        tick = walk.instrument.tick_paise
        lot = walk.instrument.lot_size
        pull = (walk.anchor - walk.ltp) / (tick * 200)  # mean reversion, gentle
        step = round(self._rng.gauss(pull, 1.2))
        new = max(tick, walk.ltp + step * tick)
        walk.up = new >= walk.ltp
        walk.ltp = new
        walk.high, walk.low = max(walk.high, new), min(walk.low, new)
        traded = lot * self._rng.randint(1, 8)
        walk.volume += traded
        best_bid = new if not walk.up else new - tick
        best_ask = best_bid + tick
        bids = tuple(
            DepthLevel(best_bid - i * tick, lot * self._rng.randint(1, 12), self._rng.randint(1, 9))
            for i in range(5)
        )
        asks = tuple(
            DepthLevel(best_ask + i * tick, lot * self._rng.randint(1, 12), self._rng.randint(1, 9))
            for i in range(5)
        )
        now = self._clock()
        return Tick(
            instrument_id=walk.instrument.id,
            ltp_paise=new,
            received_at=now,
            last_qty=traded,
            avg_price_paise=(walk.high + walk.low) // 2,
            volume=walk.volume,
            buy_qty=sum(level.qty for level in bids) * 20,
            sell_qty=sum(level.qty for level in asks) * 20,
            open_paise=walk.open,
            high_paise=walk.high,
            low_paise=walk.low,
            close_paise=walk.close,
            last_trade_time=now,
            exchange_time=now,
            bids=bids,
            asks=asks,
        )
