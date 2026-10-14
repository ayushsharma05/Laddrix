"""The algos MockTrade ships with. Add one here (spec + strategy) and every screen shows it."""

from mocktrade.algos import iceberg, pov, schedule, slicer, spreader
from mocktrade.algos.engine import AlgoType

BUILTIN: tuple[AlgoType, ...] = (
    AlgoType(slicer.SPEC, slicer.Slicer),
    AlgoType(schedule.TWAP_SPEC, schedule.Twap),
    AlgoType(schedule.VWAP_SPEC, schedule.Vwap),
    AlgoType(pov.SPEC, pov.Pov),
    AlgoType(iceberg.SPEC, iceberg.Iceberg),
    AlgoType(spreader.SPEC, spreader.Spreader),
)
