"""How positions and algos look in JSON: shared by the API and the stream so they never differ."""

from decimal import Decimal
from fractions import Fraction
from typing import Any

from mocktrade.algos.engine import AlgoView
from mocktrade.positions.book import PositionView


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
    }
