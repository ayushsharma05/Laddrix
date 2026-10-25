"""Volume at price (VAP): how much traded today at each price, for the ladder's VAP column.

Kite doesn't send trades one by one, only snapshots (~1 per second) carrying the day's
total volume and the last price. So between two snapshots we know *how much* traded but
not exactly *where*: we put the whole difference on the newer snapshot's last price.
That's an estimate, the same one professional ladders make on snapshot feeds.

    tick 1: volume 1,000  ltp 100.00   -> baseline only (we don't know what traded before)
    tick 2: volume 1,150  ltp 100.05   -> +150 at 100.05
    tick 3: volume 1,150  ltp 100.00   -> nothing traded, nothing added
    tick 4: volume   300               -> volume went backwards: a new day, start again
"""

from dataclasses import dataclass, field

from laddrix.domain.instruments import InstrumentId
from laddrix.domain.ticks import Tick


@dataclass
class _Day:
    volume: int  # the day's total volume at the last tick we counted
    levels: dict[int, int] = field(default_factory=dict)  # price in paise -> qty


class VolumeAtPrice:
    def __init__(self) -> None:
        self._days: dict[InstrumentId, _Day] = {}

    def on_tick(self, tick: Tick) -> None:
        if tick.volume is None:
            return  # a light-mode tick carries no volume: nothing to learn
        day = self._days.get(tick.instrument_id)
        if day is None or tick.volume < day.volume:
            # First sight (we can't know where earlier volume traded) or a new trading day.
            self._days[tick.instrument_id] = _Day(volume=tick.volume)
            return
        traded = tick.volume - day.volume
        if traded:
            day.levels[tick.ltp_paise] = day.levels.get(tick.ltp_paise, 0) + traded
            day.volume = tick.volume

    def snapshot(self, instrument_id: InstrumentId) -> tuple[int | None, dict[int, int]]:
        """(the day's volume at the last counted tick, {price: qty}); (None, {}) if unseen.

        The volume lets a screen continue the count from the stream without double counting:
        it adds only what trades after this volume.
        """
        day = self._days.get(instrument_id)
        if day is None:
            return None, {}
        return day.volume, dict(day.levels)
