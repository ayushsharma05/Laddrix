"""Live implied spread prices, as ticks of "SPREAD:<id>" (#414).

One pricer per price source (each Kite subscription has its own, #395), so a spread's price
comes from its trader's own prices like everything else:

    a screen watches SPREAD:x ─► first watcher (on_demand) ─► watch its legs on this source
    a leg ticks               ─► every watched spread with that leg: implied() ─► a derived
                                 tick of SPREAD:x into the same source ─► its watchers (screens)
                                 (never the recorder, paper fills, charts or alerts: they see
                                 only real market prices, MarketData.on_derived)
    the definition changes    ─► re-index its legs, publish at once; deleted: published empty

The pricer only adds ticks; it never changes a leg's. Its work per leg tick is a dict lookup,
plus one sum per watched spread that has that leg.
"""

import logging
from collections.abc import Callable

from laddrix.domain.instruments import Instrument
from laddrix.domain.ticks import Tick
from laddrix.marketdata.service import MarketData, Subscription
from laddrix.spreads.model import PREFIX, SpreadDef, as_tick, implied
from laddrix.spreads.store import SpreadBook

log = logging.getLogger(__name__)


class SpreadPricer:
    def __init__(self, prices: MarketData, book: SpreadBook,
                 lookup: Callable[[str], Instrument | None]) -> None:  # fmt: skip
        self._prices = prices
        self._book = book
        self._lookup = lookup
        self._legs: dict[str, Subscription] = {}  # spread id -> the subscription to its legs
        self._by_leg: dict[str, set[str]] = {}  # leg instrument -> spreads priced from it
        prices.on_demand(self._on_demand)
        prices.tap(self._on_tick)
        book.subscribe(self._on_change)

    def _on_demand(self, instrument_id: str) -> None:
        spread = self._book.of_instrument(instrument_id)
        if spread is not None:
            self._start(spread)

    def _start(self, spread: SpreadDef) -> None:
        self._stop(spread.spread_id)
        ids = [leg.instrument_id for leg in spread.legs]
        # watched so the feed streams them; never read: the ticks come through the tap
        self._legs[spread.spread_id] = self._prices.subscribe(ids)
        for leg in ids:
            self._by_leg.setdefault(leg, set()).add(spread.spread_id)
        self.publish(spread)

    def _stop(self, spread_id: str) -> None:
        old = self._legs.pop(spread_id, None)
        if old is not None:
            old.close()
        for leg in [k for k, v in self._by_leg.items() if spread_id in v]:
            self._by_leg[leg].discard(spread_id)
            if not self._by_leg[leg]:
                del self._by_leg[leg]

    def _on_tick(self, tick: Tick) -> None:
        for spread_id in tuple(self._by_leg.get(tick.instrument_id, ())):
            spread = self._book.get(spread_id)
            if spread is not None:
                self.publish(spread)

    def _on_change(self, spread_id: str, spread: SpreadDef | None) -> None:
        if spread is None:
            self._stop(spread_id)
            # what screens still show goes blank: no price for a spread that is gone
            self._prices.on_derived(Tick(PREFIX + spread_id, 0))
        elif self._prices.is_watched(spread.instrument_id) or spread_id in self._legs:
            self._start(spread)  # new legs: watched and priced at once

    def publish(self, spread: SpreadDef) -> None:
        now = implied(spread, self._prices.latest, self._lookup, self._prices.age_s)
        self._prices.on_derived(as_tick(spread, now))

    def pricing(self) -> set[str]:
        """The spreads priced now (for tests and the ops screen)."""
        return set(self._legs)
