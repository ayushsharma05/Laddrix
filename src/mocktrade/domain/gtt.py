"""GTT (good till triggered): an order the BROKER holds until a price is touched.

Unlike MockTrade's own synthetic stops (algos/protect.py), a GTT lives at the broker, so it
protects a position while our server is down. In exchange it is simpler: when the last
price touches a trigger, the broker places a limit order, and that's all.

    SINGLE  one trigger, one order        e.g. a stop-loss sell below the market
    OCO     two triggers, two orders      a stop below and a target above (or the mirror);
                                          the first touched places its order, the other ends

The broker publishes what it allows (`GttSpec`), the screens draw from it, and the server
checks a request against it before anything is sent, with every problem in words.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from mocktrade.domain.instruments import Instrument, InstrumentId
from mocktrade.domain.orders import Product, Side


class GttKind(Enum):
    SINGLE = "single"
    OCO = "two-leg"  # Kite's name for it


class GttStatus(Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    DISABLED = "disabled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    DELETED = "deleted"

    @property
    def is_final(self) -> bool:
        return self is not GttStatus.ACTIVE


@dataclass(frozen=True)
class GttLeg:
    trigger_paise: int  # the last price that fires this leg
    side: Side
    qty: int
    price_paise: int  # the limit order placed when it fires


@dataclass(frozen=True)
class GttRequest:
    account_id: str
    user_id: str
    instrument_id: InstrumentId
    product: Product
    kind: GttKind
    legs: tuple[GttLeg, ...]  # OCO: the lower trigger first


@dataclass(frozen=True)
class GttView:
    gtt_id: str  # the broker's id
    request: GttRequest
    status: GttStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None
    order_id: str | None = None  # the broker order it placed, once triggered
    fired_leg: int | None = None  # which leg fired (0 or 1)
    reason: str = ""


@dataclass(frozen=True)
class GttSpec:
    """What the broker allows for GTTs on one instrument (drawn by the screens)."""

    instrument_id: InstrumentId
    kinds: tuple[GttKind, ...]
    tick_paise: int
    lot_size: int
    products: tuple[Product, ...]
    min_distance_pct: float  # how far each trigger must be from the last price
    expiry_days: int
    max_active: int
    help: dict[str, str] = field(default_factory=dict)


def standard_gtt_spec(instrument: Instrument, products: tuple[Product, ...]) -> GttSpec:
    """Kite's GTT rules (checked 15 Oct 2026; re-check before going live)."""
    return GttSpec(
        instrument_id=instrument.id,
        kinds=(GttKind.SINGLE, GttKind.OCO),
        tick_paise=instrument.tick_paise,
        lot_size=instrument.lot_size,
        products=products,
        min_distance_pct=0.25,
        expiry_days=365,
        max_active=250,
        help={
            GttKind.SINGLE.value: "One trigger: when the last price touches it, a limit order "
            "is placed at your price. Held by the broker: works even if MockTrade is down.",
            GttKind.OCO.value: "A stop and a target: whichever price is touched first places "
            "its limit order, and the other is cancelled. Held by the broker.",
        },
    )


def gtt_problems(spec: GttSpec, request: GttRequest, ltp_paise: int | None) -> list[str]:
    """Every reason the broker would refuse this GTT, in words (empty: fine)."""
    problems: list[str] = []
    want = 2 if request.kind is GttKind.OCO else 1
    if request.kind not in spec.kinds:
        problems.append(f"{request.kind.value} GTTs are not offered on {spec.instrument_id}")
    if len(request.legs) != want:
        problems.append(f"a {request.kind.value} GTT has {want} leg{'s' * (want > 1)}")
        return problems
    if request.product not in spec.products:
        problems.append(f"{request.product.value} is not available on {spec.instrument_id}")
    for n, leg in enumerate(request.legs, 1):
        name = f"leg {n}" if want > 1 else "the GTT"
        if leg.qty <= 0 or leg.qty % spec.lot_size:
            problems.append(f"{name}: quantity must be whole lots of {spec.lot_size}")
        for what, value in (("trigger", leg.trigger_paise), ("price", leg.price_paise)):
            if value <= 0 or value % spec.tick_paise:
                problems.append(f"{name}: {what} must be on the {spec.tick_paise / 100:.2f} grid")
        if ltp_paise:
            gap = abs(leg.trigger_paise - ltp_paise) / ltp_paise * 100
            if gap < spec.min_distance_pct:
                problems.append(
                    f"{name}: trigger {leg.trigger_paise / 100:,.2f} is only {gap:.2f}% from the "
                    f"last price {ltp_paise / 100:,.2f} (at least {spec.min_distance_pct}%)"
                )
    if want == 2:
        low, high = request.legs
        if low.trigger_paise >= high.trigger_paise:
            problems.append("an OCO's first leg must have the lower trigger")
        elif ltp_paise and not low.trigger_paise < ltp_paise < high.trigger_paise:
            problems.append("an OCO needs one trigger below the last price and one above")
        if low.side is not high.side:
            problems.append("both legs of an OCO close the same position: same side")
    return problems


def gtt_to_dict(view: GttView) -> dict[str, Any]:
    r = view.request
    return {
        "gtt_id": view.gtt_id,
        "account_id": r.account_id,
        "instrument_id": r.instrument_id,
        "product": r.product.value,
        "kind": r.kind.value,
        "legs": [
            {
                "trigger_paise": leg.trigger_paise,
                "side": leg.side.value,
                "qty": leg.qty,
                "price_paise": leg.price_paise,
            }
            for leg in r.legs
        ],  # fmt: skip
        "status": view.status.value,
        "order_id": view.order_id,
        "fired_leg": view.fired_leg,
        "reason": view.reason,
        "created_at": view.created_at.isoformat() if view.created_at else None,
        "updated_at": view.updated_at.isoformat() if view.updated_at else None,
    }


def gtt_spec_to_dict(spec: GttSpec) -> dict[str, Any]:
    return {
        "instrument_id": spec.instrument_id,
        "kinds": [k.value for k in spec.kinds],
        "tick_paise": spec.tick_paise,
        "lot_size": spec.lot_size,
        "products": [p.value for p in spec.products],
        "min_distance_pct": spec.min_distance_pct,
        "expiry_days": spec.expiry_days,
        "max_active": spec.max_active,
        "help": dict(spec.help),
    }
