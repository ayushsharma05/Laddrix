"""The algos MockTrade ships with. Add one here (spec + strategy) and every screen shows it."""

from mocktrade.algos import slicer, spreader
from mocktrade.algos.engine import AlgoType

BUILTIN: tuple[AlgoType, ...] = (
    AlgoType(slicer.SPEC, slicer.Slicer),
    AlgoType(spreader.SPEC, spreader.Spreader),
)
