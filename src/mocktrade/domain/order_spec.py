"""What can be ordered on an instrument: the order spec the screens are built from.

"Backend provides, frontend displays." The browser never hard-codes order types, their
fields or the market's rules. It asks the server for an instrument's order spec and draws
its ticket and ladder from it. A broker or exchange that adds an order type (or drops one)
changes this description, and every screen follows without a new release.

    order spec ─► which order types exist (label, name, what each needs)
               ─► which validities each type allows, which products the instrument takes
               ─► tick and lot size, and the extra rules a type carries (e.g. a stop's
                  trigger must not have been passed by the market already)

Brokers can publish their own spec (Kite and paper share `standard_spec`). Order requests
are still checked on the server; the spec only lets the screen explain a problem before
the click.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from mocktrade.domain.instruments import Instrument, InstrumentKind
from mocktrade.domain.orders import OrderType, Product, Validity


class Need(Enum):
    REQUIRED = "required"  # the trader must give it
    NONE = "none"  # the type must not carry it
    AUTO = "auto"  # the system fills it in (e.g. market protection)


class Rule(Enum):
    """Extra checks a type carries, by name, so screens can explain them up front."""

    STOP_TRIGGER = "stop_trigger"  # buy trigger above the last price, sell trigger below
    STOP_LIMIT = "stop_limit"  # buy trigger ≤ limit, sell trigger ≥ limit


@dataclass(frozen=True)
class OrderTypeSpec:
    code: OrderType
    label: str  # short, for buttons: "LMT"
    name: str  # in words: "Limit"
    help: str  # one sentence a trader reads on hover
    price: Need
    trigger: Need
    market_protection: Need
    validities: tuple[Validity, ...]
    rules: tuple[Rule, ...] = ()
    priced_by_click: bool = False  # the ladder can place it: a click gives its price (or none)


@dataclass(frozen=True)
class OrderSpec:
    instrument_id: str
    tradable: bool
    tick_paise: int
    lot_size: int
    order_types: tuple[OrderTypeSpec, ...]
    products: tuple[Product, ...]
    auto_protection_pct: Decimal  # what automatic market protection means (for "pays at most")
    max_modifications: int
    freeze_qty: int | None = None  # the exchange's most per order (#424); None: not known


LIMIT = OrderTypeSpec(
    OrderType.LIMIT, "LMT", "Limit", "Trades at your price or better; the rest waits in the queue.",
    price=Need.REQUIRED, trigger=Need.NONE, market_protection=Need.NONE,
    validities=(Validity.DAY, Validity.IOC), priced_by_click=True,
)  # fmt: skip
MARKET = OrderTypeSpec(
    OrderType.MARKET, "MKT", "Market",
    "Trades now at the best prices, never beyond the automatic protection band.",
    price=Need.NONE, trigger=Need.NONE, market_protection=Need.AUTO,
    validities=(Validity.DAY, Validity.IOC), priced_by_click=True,
)  # fmt: skip
STOP_LIMIT = OrderTypeSpec(
    OrderType.SL, "SL", "Stop-loss limit",
    "Waits until the price reaches the trigger, then works as a limit at your price.",
    price=Need.REQUIRED, trigger=Need.REQUIRED, market_protection=Need.NONE,
    validities=(Validity.DAY,), rules=(Rule.STOP_TRIGGER, Rule.STOP_LIMIT),
)  # fmt: skip
STOP_MARKET = OrderTypeSpec(
    OrderType.SL_M, "SL-M", "Stop-loss market",
    "Waits until the price reaches the trigger, then trades like a protected market order.",
    price=Need.NONE, trigger=Need.REQUIRED, market_protection=Need.AUTO,
    validities=(Validity.DAY,), rules=(Rule.STOP_TRIGGER,),
)  # fmt: skip

STANDARD_TYPES = (LIMIT, MARKET, STOP_LIMIT, STOP_MARKET)


def products_for(instrument: Instrument) -> tuple[Product, ...]:
    """Delivery for shares, overnight for F&O; intraday for both. Indices can't be traded."""
    if instrument.kind is InstrumentKind.INDEX:
        return ()
    if instrument.kind is InstrumentKind.EQUITY:
        return (Product.CNC, Product.MIS)
    return (Product.NRML, Product.MIS)


def standard_spec(
    instrument: Instrument, *, auto_protection_pct: Decimal, max_modifications: int
) -> OrderSpec:
    """The NSE/BSE order types every Indian broker offers (Kite and paper use this)."""
    return OrderSpec(
        instrument_id=instrument.id,
        tradable=instrument.tradable,
        tick_paise=instrument.tick_paise,
        lot_size=instrument.lot_size,
        order_types=STANDARD_TYPES if instrument.tradable else (),
        products=products_for(instrument),
        auto_protection_pct=auto_protection_pct,
        max_modifications=max_modifications,
        freeze_qty=instrument.max_order_qty,
    )


def spec_problem(
    spec: OrderSpec, order_type: OrderType, validity: Validity, product: Product
) -> str | None:
    """Why the spec doesn't allow this combination, in words, or None."""
    if not spec.tradable:
        return f"{spec.instrument_id} can't be traded"
    found = next((t for t in spec.order_types if t.code is order_type), None)
    if found is None:
        return f"{order_type.value} orders aren't offered on {spec.instrument_id}"
    if validity not in found.validities:
        allowed = ", ".join(v.value for v in found.validities)
        return f"{found.name} orders can't be {validity.value} (allowed: {allowed})"
    if product not in spec.products:
        allowed = ", ".join(p.value for p in spec.products)
        return f"product {product.value} isn't offered on {spec.instrument_id} (allowed: {allowed})"
    return None


def spec_to_dict(spec: OrderSpec) -> dict:
    return {
        "instrument_id": spec.instrument_id,
        "tradable": spec.tradable,
        "tick_paise": spec.tick_paise,
        "lot_size": spec.lot_size,
        "order_types": [
            {
                "code": t.code.value,
                "label": t.label,
                "name": t.name,
                "help": t.help,
                "price": t.price.value,
                "trigger": t.trigger.value,
                "market_protection": t.market_protection.value,
                "validities": [v.value for v in t.validities],
                "rules": [r.value for r in t.rules],
                "priced_by_click": t.priced_by_click,
            }
            for t in spec.order_types
        ],
        "products": [p.value for p in spec.products],
        "auto_protection_pct": str(spec.auto_protection_pct),
        "max_modifications": spec.max_modifications,
        "freeze_qty": spec.freeze_qty,
    }
