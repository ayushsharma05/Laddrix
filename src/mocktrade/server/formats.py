"""How positions, algos, instruments and audit events look in JSON: shared by the API and the
stream so they never differ."""

from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from typing import Any

from mocktrade.algos.engine import AlgoView
from mocktrade.domain.instruments import Instrument
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.positions.book import PositionView
from mocktrade.refdata.expiry import Expiries, expiry_to_dict
from mocktrade.spreads.model import SpreadDef, leg_to_dict, spread_tick
from mocktrade.store.events import StoredEvent


def position_to_dict(p: PositionView) -> dict[str, Any]:
    return {
        "instrument_id": p.instrument_id,
        "product": p.product.value,
        "qty": p.qty,
        "avg_price_paise": decimal_text(p.avg_price_paise),
        "ltp_paise": p.ltp_paise,
        "realised_paise": p.realised_paise,
        "unrealised_paise": p.unrealised_paise,
        "pnl_paise": p.pnl_paise,
        "buy_qty": p.buy_qty,
        "sell_qty": p.sell_qty,
        "buy_value_paise": p.buy_value_paise,
        "sell_value_paise": p.sell_value_paise,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "carried_avg_paise": (
            decimal_text(p.carried_avg_paise) if p.carried_avg_paise is not None else None
        ),
    }


def decimal_text(value: Fraction) -> str:
    """An exact average like 10007.5 paise as text, at most 4 decimals (1/3 can't be exact)."""
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return f"{exact.quantize(Decimal('0.0001')).normalize():f}"  # never "1E+4"


def algo_to_dict(a: AlgoView) -> dict[str, Any]:
    return {
        "algo_id": a.algo_id,
        "code": a.code,
        "account_id": a.account_id,
        "user_id": a.user_id,
        "instrument_id": a.instrument_id,
        "side": a.side.value,
        "qty": a.qty,
        "params": a.params,
        "state": a.state.value,
        "filled_qty": a.filled_qty,
        "avg_price_paise": a.avg_price_paise,
        "working_qty": a.working_qty,
        "children": list(a.children),
        "reason": a.reason,
        "status": a.status,
        "extra": a.extra,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "actions": [{"name": x.name, "label": x.label, "help": x.help} for x in a.actions],
    }


def instrument_to_dict(i: Instrument, expiries: Expiries | None = None) -> dict[str, Any]:
    """An instrument as JSON; with `expiries`, its expiry facts now as well (#290)."""
    return {
        "id": i.id,
        "exchange": i.exchange.value,
        "symbol": i.symbol,
        "kind": i.kind.value,
        "tick_paise": i.tick_paise,
        "lot_size": i.lot_size,
        "expiry": i.expiry.isoformat() if i.expiry else None,
        "strike_paise": i.strike_paise,
        "underlying": i.underlying,
        "tradable": i.tradable,
        "freeze_qty": i.freeze_qty,
        "expiry_info": expiry_to_dict(expiries.info(i)) if expiries else None,
    }


def spread_instrument_to_dict(
    s: SpreadDef, lookup: Callable[[str], Instrument | None]
) -> dict[str, Any]:
    """A spread as an instrument, for the screens: one spread lot is one "lot" (#414)."""
    return {
        "id": s.instrument_id,
        "exchange": "SPREAD",
        "symbol": s.name,
        "kind": "SPREAD",
        "tick_paise": spread_tick(s.legs, lookup),
        "lot_size": 1,
        "expiry": None,
        "strike_paise": None,
        "underlying": None,
        "tradable": True,  # a spread ladder's click starts the ASE algo (#416)
        "freeze_qty": None,  # each leg has its own; the ASE splits within them (#424)
        "expiry_info": None,
        "spread": {"spread_id": s.spread_id, "legs": [leg_to_dict(x) for x in s.legs]},
    }


def event_to_dict(e: StoredEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "ts": e.ts.isoformat(),
        "account_id": e.account_id,
        "kind": e.kind,
        "client_order_id": e.client_order_id,
        "detail": e.detail,
        "order": view_to_dict(e.order) if e.order else None,
        "fill": fill_to_dict(e.fill) if e.fill else None,
        "extra": e.extra,
    }
