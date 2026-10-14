"""What an algo needs from the trader: the algo spec the screens are built from.

Same rule as the order spec ("backend provides, frontend displays"): every algo publishes
its parameters (name, label, kind, limits, default, help), the algo screen draws its form
from them, and the engine checks the values against the same description. A new algo, or a
new parameter, needs no screen change.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ParamKind(Enum):
    LOTS = "lots"  # whole lots of the instrument
    PRICE = "price"  # paise, on the instrument's tick grid
    PRICE_OFFSET = "price_offset"  # paise, may be negative (e.g. a spread level)
    TICKS = "ticks"  # a number of ticks (e.g. pay-up)
    SECONDS = "seconds"
    COUNT = "count"
    PERCENT = "percent"
    CHOICE = "choice"
    FLAG = "flag"
    INSTRUMENT = "instrument"  # another instrument (e.g. a spread's second leg)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    kind: ParamKind
    help: str
    required: bool = True
    default: Any = None
    min: int | float | None = None
    max: int | float | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlgoSpec:
    code: str  # "SLICER"
    name: str  # "Slicer"
    help: str  # one sentence
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)


def check_params(spec: AlgoSpec, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Clean values (defaults filled in) and the problems, each a sentence naming the field.

    Unknown names are refused (no smuggling options the screen didn't show).
    """
    problems: list[str] = []
    known = {p.name for p in spec.params}
    for name in sorted(set(raw) - known):
        problems.append(f"{spec.name} has no setting called {name!r}")
    clean: dict[str, Any] = {}
    for p in spec.params:
        value = raw.get(p.name, p.default)
        if value is None:
            if p.required:
                problems.append(f"{p.label} is required")
            continue
        try:
            value = _coerce(p, value)
        except (TypeError, ValueError) as exc:
            problems.append(f"{p.label}: {exc}")
            continue
        if p.min is not None and value < p.min:
            problems.append(f"{p.label} must be at least {p.min}")
        if p.max is not None and value > p.max:
            problems.append(f"{p.label} must be at most {p.max}")
        clean[p.name] = value
    return clean, problems


def _coerce(p: ParamSpec, value: Any) -> Any:
    if p.kind is ParamKind.FLAG:
        if not isinstance(value, bool):
            raise ValueError("must be yes or no")
        return value
    if p.kind in (ParamKind.CHOICE, ParamKind.INSTRUMENT):
        if not isinstance(value, str) or not value:
            raise ValueError("must be chosen")
        if p.kind is ParamKind.CHOICE and value not in p.choices:
            raise ValueError(f"must be one of {', '.join(p.choices)}")
        return value
    if p.kind in (ParamKind.SECONDS, ParamKind.PERCENT):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("must be a number")
        return float(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a whole number")
    return value


def spec_to_dict(spec: AlgoSpec) -> dict[str, Any]:
    return {
        "code": spec.code,
        "name": spec.name,
        "help": spec.help,
        "params": [
            {
                "name": p.name,
                "label": p.label,
                "kind": p.kind.value,
                "help": p.help,
                "required": p.required,
                "default": p.default,
                "min": p.min,
                "max": p.max,
                "choices": list(p.choices),
            }
            for p in spec.params
        ],
    }
