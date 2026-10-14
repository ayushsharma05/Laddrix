"""The algos MockTrade ships with. Add one here (spec + strategy) and every screen shows it."""

from mocktrade.algos import slicer
from mocktrade.algos.engine import AlgoType

BUILTIN: tuple[AlgoType, ...] = (AlgoType(slicer.SPEC, slicer.Slicer),)
