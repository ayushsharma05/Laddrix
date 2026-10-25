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
    MINUTES = "minutes"
    COUNT = "count"
    PERCENT = "percent"
    CHOICE = "choice"
    FLAG = "flag"
    INSTRUMENT = "instrument"  # another instrument (e.g. a spread's second leg)
    MONEY = "money"  # paise, shown in rupees (e.g. a limit per order)
    CHOICES = "choices"  # several of `choices` (min/max: how many)
    INSTRUMENTS = "instruments"  # a list of instruments (min/max: how many)
    SPREAD = "spread"  # one of the trader's spreads (#415): its id


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
    # how screens group algos: "execution" (work an order), "protection", "spread" (started
    # from a spread ladder, #416: not offered in the generic algo form)
    family: str = "execution"


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
        size = len(value) if isinstance(value, list) else value  # lists: how many
        if p.min is not None and size < p.min:
            what = f"at least {p.min} chosen" if isinstance(value, list) else f"at least {p.min}"
            problems.append(f"{p.label} must be {what}")
        if p.max is not None and size > p.max:
            what = f"at most {p.max} chosen" if isinstance(value, list) else f"at most {p.max}"
            problems.append(f"{p.label} must be {what}")
        clean[p.name] = value
    return clean, problems


def _coerce(p: ParamSpec, value: Any) -> Any:
    if p.kind is ParamKind.FLAG:
        if not isinstance(value, bool):
            raise ValueError("must be yes or no")
        return value
    if p.kind in (ParamKind.CHOICE, ParamKind.INSTRUMENT, ParamKind.SPREAD):
        if not isinstance(value, str) or not value:
            raise ValueError("must be chosen")
        if p.kind is ParamKind.CHOICE and value not in p.choices:
            raise ValueError(f"must be one of {', '.join(p.choices)}")
        return value
    if p.kind in (ParamKind.CHOICES, ParamKind.INSTRUMENTS):
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) and v for v in value):
            raise ValueError("must be a list")
        if p.kind is ParamKind.CHOICES:
            bad = [v for v in value if v not in p.choices]
            if bad:
                raise ValueError(f"{', '.join(bad)}: not one of {', '.join(p.choices)}")
        return sorted(set(value))  # each once, in a stable order
    if p.kind in (ParamKind.SECONDS, ParamKind.MINUTES, ParamKind.PERCENT):
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
        "family": spec.family,
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
