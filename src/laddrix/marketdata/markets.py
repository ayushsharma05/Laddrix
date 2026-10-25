"""What the screens read from one price source (#395): one per subscription.

    a source (MarketData) ─► its Market: option chain, time and sales, movers, volume at
                             price, charts, recorded days. Built the first time someone needs
                             it, then fed by its own source's ticks only.

The server asks `Markets.of(source)` with the asker's own source (PriceSources.for_user), so
one person's chart, chain or movers list is never computed from another's subscription. On a
simulator or a replay there is one source, so one Market, as before.
"""

from collections.abc import Callable
from dataclasses import dataclass

from laddrix.charts.service import Charts
from laddrix.marketdata.movers import Movers
from laddrix.marketdata.prints import TimeAndSales
from laddrix.marketdata.service import MarketData
from laddrix.options.chain import ChainService
from laddrix.refdata.store import InstrumentStore
from laddrix.research.ticks import TickStore


@dataclass
class Market:
    prices: MarketData
    chains: ChainService
    prints: TimeAndSales
    movers: Movers
    charts: Charts | None
    ticks: TickStore | None = None  # this source's recorded days (#396)


class Markets:
    def __init__(
        self,
        instruments: InstrumentStore,
        *,
        charts_for: Callable[[MarketData], Charts | None] = lambda _md: None,
        ticks_for: Callable[[MarketData], TickStore | None] = lambda _md: None,
    ) -> None:
        self._instruments = instruments
        self._charts_for = charts_for
        self._ticks_for = ticks_for
        self._by_source: dict[int, Market] = {}

    def of(self, prices: MarketData) -> Market:
        found = self._by_source.get(id(prices))
        if found is None:
            prints = TimeAndSales(wanted=prices.is_watched)  # trades between ticks, watched only
            prices.tap(prints.on_tick)
            movers = Movers(prices.all_latest, self._instruments.get)
            prices.tap(movers.on_tick)
            found = Market(prices, ChainService(self._instruments, prices), prints, movers,
                           self._charts_for(prices), self._ticks_for(prices))  # fmt: skip
            self._by_source[id(prices)] = found
        return found
