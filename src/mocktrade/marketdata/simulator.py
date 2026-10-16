"""A market simulator: realistic ticks without Kite, for the demo server and UI work.

Each instrument does a random walk on its tick grid, gently pulled back to where it
started, and publishes ticks shaped like Kite's full mode: last price and quantity,
5 levels of depth each side, cumulative volume, open/high/low and the previous close.
About one tick per instrument per second, like Kite. Seeded, so tests are repeatable.

Options don't walk on their own: each is priced from its underlying's future with Black-76
and a volatility smile (higher IV away from the money, more on the put side), plus a tick
of noise and slowly changing open interest, so an option chain built from the simulator
looks like a real one (sensible IVs, Greeks, put-call parity).

It never trades with you: the paper broker fills against these prices exactly as it
would against Kite's.
"""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mocktrade.domain.instruments import Instrument, InstrumentId, InstrumentKind
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData

_OPTIONS = (InstrumentKind.CALL, InstrumentKind.PUT)


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
    oi: int = 0  # open interest (options)


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
        # futures first: options are priced from their future's newest price
        ordered = sorted(start_prices.items(), key=lambda kv: kv[0].kind in _OPTIONS)
        for instrument, price in ordered:
            if price <= 0 or price % instrument.tick_paise:
                raise ValueError(f"{instrument.id}: start price must be on the tick grid")
            close = price - instrument.tick_paise * self._rng.randint(-40, 40)
            close = max(close, instrument.tick_paise)
            self._walks[instrument.id] = _Walk(
                instrument, price, price, close, close, max(price, close), min(price, close)
            )
            if instrument.kind in _OPTIONS:
                walk = self._walks[instrument.id]
                walk.oi = instrument.lot_size * self._rng.randint(200, 4000)
                # an option trades at its model price: its day (open, close) starts there too,
                # so its change from the close is a believable one (not from a placeholder)
                priced = self._option_price(walk)
                if priced is not None:
                    close = max(priced - instrument.tick_paise * self._rng.randint(-40, 40),
                                instrument.tick_paise)  # fmt: skip
                    walk.anchor = walk.ltp = walk.open = priced
                    walk.close = close
                    walk.high, walk.low = max(priced, close), min(priced, close)

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

    def _future_for(self, option: Instrument) -> _Walk | None:
        futures = [
            w
            for w in self._walks.values()
            if w.instrument.kind is InstrumentKind.FUTURE
            and w.instrument.underlying == option.underlying
            and w.instrument.expiry is not None
        ]
        if not futures or option.expiry is None:
            return None
        return min(futures, key=lambda w: abs((w.instrument.expiry - option.expiry).days))

    def _option_price(self, walk: _Walk) -> int | None:
        """Black-76 off the future with a smile, on the tick grid; None without a future."""
        from mocktrade.options.model import fair_value_paise  # options build on market data

        option = walk.instrument
        future = self._future_for(option)
        if future is None or option.strike_paise is None:
            return None
        moneyness = math.log(option.strike_paise / future.ltp)
        sigma = 0.12 + 0.6 * moneyness * moneyness - 0.08 * moneyness  # smile, put skew
        fair = fair_value_paise(option, future.ltp, sigma, self._clock())
        noise = self._rng.choice((-1, 0, 0, 1)) * option.tick_paise
        return max(option.tick_paise, round(fair / option.tick_paise) * option.tick_paise + noise)

    def _move(self, walk: _Walk) -> Tick:
        tick = walk.instrument.tick_paise
        lot = walk.instrument.lot_size
        priced = self._option_price(walk) if walk.instrument.kind in _OPTIONS else None
        if priced is not None:
            new = priced
            walk.oi = max(0, walk.oi + lot * self._rng.randint(-20, 24))
        else:
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
            open_interest=walk.oi if walk.instrument.kind in _OPTIONS else None,
            last_trade_time=now,
            exchange_time=now,
            bids=bids,
            asks=asks,
        )
