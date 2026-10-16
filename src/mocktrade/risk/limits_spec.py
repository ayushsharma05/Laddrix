"""The risk limits as a spec: what an admin can set on an account, for the admin screen.

Same rule as the order and algo specs ("backend provides, frontend displays"): every limit is
published with its label, kind, range, default and help, the admin screen draws its form from
that, and the server checks what comes back against the same description.

    flat names   max_order_lots, max_order_value_paise, ..., price_band_pct.EQ, price_band_pct.FUT
    to a dict    limits_to_dict(RiskLimits()) -> {"max_order_lots": 10, ...}
    from a dict  limits_from_dict(raw) -> (RiskLimits | None, problems)

`changes(old, new)` says what an edit changes, each change marked looser or tighter, so the
screen can ask twice before a limit is loosened; `warnings(...)` says what a tighter limit
means for what is open now (it never cancels anything by itself).
"""

from decimal import Decimal
from typing import Any

from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec, check_params, spec_to_dict
from mocktrade.domain.instruments import Exchange, InstrumentKind
from mocktrade.domain.money import format_rupees
from mocktrade.risk.limits import RiskLimits

K = InstrumentKind
BAND = "price_band_pct."  # one percent per instrument kind: price_band_pct.EQ, ...
TRADED_KINDS = (K.EQUITY, K.FUTURE, K.CALL, K.PUT)
D = RiskLimits()  # the defaults the spec shows

# looser when the number goes up ("up"), down ("down"), more are allowed ("more"),
# fewer are blocked ("fewer"), or the switch is on ("on") / off ("off")
_LOOSER: dict[str, str] = {
    "allowed_exchanges": "more", "allowed_kinds": "more", "blocked_instruments": "fewer",
    "allow_option_selling": "on", "max_order_lots": "up", "max_order_value_paise": "up",
    "max_position_lots": "up", "max_open_orders": "up", "require_market_price": "off",
    "max_daily_loss_paise": "up", "duplicate_window_s": "down",
}  # fmt: skip

P = ParamSpec
LIMITS_SPEC = AlgoSpec(
    "RISK_LIMITS",
    "Risk limits",
    "What the pre-trade checks allow on this account; an order breaking one is refused with "
    "the reason.",
    (
        P(
            "allowed_exchanges",
            "Exchanges",
            ParamKind.CHOICES,
            "Where orders may go",
            default=sorted(e.value for e in D.allowed_exchanges),
            min=1,
            choices=tuple(sorted(e.value for e in Exchange)),
        ),
        P(
            "allowed_kinds",
            "Instrument kinds",
            ParamKind.CHOICES,
            "What may be traded",
            default=sorted(k.value for k in D.allowed_kinds),
            min=1,
            choices=tuple(sorted(k.value for k in TRADED_KINDS)),
        ),
        P(
            "blocked_instruments",
            "Blocked contracts",
            ParamKind.INSTRUMENTS,
            "Contracts no order may use, e.g. a stock under a ban",
            default=[],
            min=0,
            max=200,
        ),
        P(
            "allow_option_selling",
            "Allow option selling",
            ParamKind.FLAG,
            "Selling options to open can lose far more than the premium",
            default=False,
        ),
        P(
            "max_order_lots",
            "Most lots per order",
            ParamKind.LOTS,
            "Bigger orders are refused (closing orders are sent in pieces of this size)",
            default=D.max_order_lots,
            min=1,
            max=10_000,
        ),
        P(
            "max_order_value_paise",
            "Most value per order",
            ParamKind.MONEY,
            "Quantity × the worst price it could fill at",
            default=D.max_order_value_paise,
            min=1_000_00,
            max=100_00_00_000_00,
        ),
        P(
            "max_position_lots",
            "Most lots held per contract",
            ParamKind.LOTS,
            "Long or short, counting working orders",
            default=D.max_position_lots,
            min=1,
            max=100_000,
        ),
        P(
            "max_open_orders",
            "Most working orders",
            ParamKind.COUNT,
            "Orders working at the broker at once",
            default=D.max_open_orders,
            min=1,
            max=500,
        ),
        *(
            P(
                f"{BAND}{k.value}",
                f"Price band: {k.value}",
                ParamKind.PERCENT,
                f"How far a {k.value} order's price may be from the last price",
                default=float(D.price_band_pct[k]),
                min=0.5,
                max=100,
            )
            for k in TRADED_KINDS
        ),
        P(
            "require_market_price",
            "Need a live price",
            ParamKind.FLAG,
            "Refuse orders that can't be checked against a live price",
            default=D.require_market_price,
        ),
        P(
            "max_daily_loss_paise",
            "Daily loss limit",
            ParamKind.MONEY,
            "Beyond this loss today only closing trades pass, and trading stops",
            default=D.max_daily_loss_paise,
            min=1_000_00,
            max=10_00_00_000_00,
        ),
        P(
            "duplicate_window_s",
            "Double-click window",
            ParamKind.SECONDS,
            "The same order again this soon is taken as a double click (0: off)",
            default=D.duplicate_window_s,
            min=0,
            max=10,
        ),
    ),  # fmt: skip
    family="limits",
)
_BY_NAME = {p.name: p for p in LIMITS_SPEC.params}


def limits_spec_dict() -> dict[str, Any]:
    return spec_to_dict(LIMITS_SPEC)


def limits_to_dict(limits: RiskLimits) -> dict[str, Any]:
    out: dict[str, Any] = {
        "allowed_exchanges": sorted(e.value for e in limits.allowed_exchanges),
        "allowed_kinds": sorted(k.value for k in limits.allowed_kinds),
        "blocked_instruments": sorted(limits.blocked_instruments),
        "allow_option_selling": limits.allow_option_selling,
        "max_order_lots": limits.max_order_lots,
        "max_order_value_paise": limits.max_order_value_paise,
        "max_position_lots": limits.max_position_lots,
        "max_open_orders": limits.max_open_orders,
        "require_market_price": limits.require_market_price,
        "max_daily_loss_paise": limits.max_daily_loss_paise,
        "duplicate_window_s": limits.duplicate_window_s,
    }
    for k in TRADED_KINDS:
        band = limits.price_band_pct.get(k)
        out[f"{BAND}{k.value}"] = float(band) if band is not None else None
    return out


def limits_from_dict(raw: dict[str, Any]) -> tuple[RiskLimits | None, list[str]]:
    """Checked against the spec (unknown names refused, ranges, kinds); missing names take the
    defaults. Returns the limits, or None with every problem."""
    clean, problems = check_params(LIMITS_SPEC, raw)
    if problems:
        return None, problems
    bands = {k: Decimal(str(clean[f"{BAND}{k.value}"])) for k in TRADED_KINDS}
    return (
        RiskLimits(
            allowed_exchanges=frozenset(Exchange(v) for v in clean["allowed_exchanges"]),
            allowed_kinds=frozenset(InstrumentKind(v) for v in clean["allowed_kinds"]),
            blocked_instruments=frozenset(clean["blocked_instruments"]),
            allow_option_selling=clean["allow_option_selling"],
            max_order_lots=clean["max_order_lots"],
            max_order_value_paise=clean["max_order_value_paise"],
            max_position_lots=clean["max_position_lots"],
            max_open_orders=clean["max_open_orders"],
            price_band_pct=bands,
            require_market_price=clean["require_market_price"],
            max_daily_loss_paise=clean["max_daily_loss_paise"],
            duplicate_window_s=float(clean["duplicate_window_s"]),
        ),
        [],
    )


def stored_limits(raw: dict[str, Any] | None) -> RiskLimits:
    """Limits saved by an earlier version: names it no longer knows are dropped, new ones take
    their default, and anything now out of range falls back to the default (said in the log
    by the caller). Never refuses to start an account."""
    if not raw:
        return RiskLimits()
    usable = {k: v for k, v in raw.items() if k in _BY_NAME and not limits_from_dict({k: v})[1]}
    return limits_from_dict(usable)[0] or RiskLimits()


def _looser(name: str, old: Any, new: Any) -> bool:
    way = _LOOSER.get(name.split(".")[0], "up")
    if name.startswith(BAND):
        way = "up"
    if way == "up":
        return new > old
    if way == "down":
        return new < old
    if way == "more":
        return not set(new) <= set(old)
    if way == "fewer":
        return not set(old) <= set(new)
    if way == "on":
        return bool(new) and not old
    return bool(old) and not new  # "off"


def _words(name: str, value: Any) -> str:
    p = _BY_NAME[name]
    if p.kind is ParamKind.MONEY:
        return format_rupees(value)
    if p.kind is ParamKind.FLAG:
        return "yes" if value else "no"
    if p.kind in (ParamKind.CHOICES, ParamKind.INSTRUMENTS):
        return ", ".join(value) if value else "none"
    if p.kind is ParamKind.PERCENT:
        return f"{value:g}%"
    if p.kind is ParamKind.SECONDS:
        return f"{value:g} s"
    return str(value)


def changes(old: RiskLimits, new: RiskLimits) -> list[dict[str, Any]]:
    """What an edit changes, in the spec's order: name, label, old and new (as values and in
    words), and whether it loosens the limit."""
    a, b = limits_to_dict(old), limits_to_dict(new)
    out = []
    for p in LIMITS_SPEC.params:
        if a[p.name] == b[p.name]:
            continue
        out.append({"name": p.name, "label": p.label, "old": a[p.name], "new": b[p.name],
                    "text": f"{p.label}: {_words(p.name, a[p.name])} → {_words(p.name, b[p.name])}",
                    "looser": _looser(p.name, a[p.name], b[p.name])})  # fmt: skip
    return out


def warnings(
    new: RiskLimits,
    *,
    positions: dict[str, tuple[int, int]],  # instrument id -> (net qty, lot size)
    working_orders: int,
    day_pnl_paise: int,
) -> list[str]:
    """What a tighter limit means for what is open now. Nothing is cancelled or closed by an
    edit; these say what the checks will do from the next order."""
    out = []
    for instrument_id, (qty, lot) in sorted(positions.items()):
        lots = abs(qty) // max(1, lot)
        if lots > new.max_position_lots:
            out.append(f"{instrument_id}: {lots} lots held, above the new {new.max_position_lots}"
                       "-lot limit: only orders that reduce it will pass")  # fmt: skip
        if instrument_id in new.blocked_instruments and qty:
            out.append(f"{instrument_id} is blocked but held: closing it is still allowed")
    if working_orders > new.max_open_orders:
        out.append(f"{working_orders} orders are working, above the new limit of "
                   f"{new.max_open_orders}: no new order until some finish")  # fmt: skip
    if day_pnl_paise <= -new.max_daily_loss_paise:
        out.append(f"today's loss of {format_rupees(-day_pnl_paise)} is already beyond the new "
                   "daily loss limit: trading stops at the next price change (the daily-loss "
                   "stop cancels working orders)")  # fmt: skip
    return out
