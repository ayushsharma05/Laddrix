"""Kite's GTT API (/gtt/triggers) in Laddrix's terms.

    POST   /gtt/triggers          type, condition (JSON), orders (JSON) -> {trigger_id}
    PUT    /gtt/triggers/{id}     the same fields
    DELETE /gtt/triggers/{id}
    GET    /gtt/triggers          every trigger with its status; a fired one carries
                                  orders[n].result.order_result.order_id

Kite has no push for GTTs: the GTT service polls the list (orders/gtt.py).
"""

import json
from datetime import datetime
from typing import Any

from laddrix.broker.kite.orders import to_kite_rupees, to_paise
from laddrix.domain.gtt import GttKind, GttLeg, GttRequest, GttStatus, GttView
from laddrix.domain.orders import Product, Side
from laddrix.options.model import IST


def gtt_params(request: GttRequest, ltp_paise: int) -> dict[str, str]:
    exchange, symbol = request.instrument_id.split(":", 1)
    condition = {
        "exchange": exchange,
        "tradingsymbol": symbol,
        "trigger_values": [float(to_kite_rupees(leg.trigger_paise)) for leg in request.legs],
        "last_price": float(to_kite_rupees(ltp_paise)),
    }
    orders = [
        {
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": leg.side.value,
            "quantity": leg.qty,
            "order_type": "LIMIT",
            "product": request.product.value,
            "price": float(to_kite_rupees(leg.price_paise)),
        }
        for leg in request.legs
    ]
    return {
        "type": request.kind.value,
        "condition": json.dumps(condition),
        "orders": json.dumps(orders),
    }


def parse_gtt(row: dict[str, Any], account_id: str, user_id: str = "") -> GttView | None:
    """One trigger from GET /gtt/triggers; None if it isn't one we understand."""
    try:
        condition = row["condition"]
        kind = GttKind(row["type"])
        status = GttStatus(row["status"])
        instrument_id = f"{condition['exchange']}:{condition['tradingsymbol']}"
        triggers = condition["trigger_values"]
        legs, order_id, fired = [], None, None
        for n, (order, trigger) in enumerate(zip(row["orders"], triggers, strict=True)):
            legs.append(
                GttLeg(
                    trigger_paise=to_paise(trigger),
                    side=Side(order["transaction_type"]),
                    qty=int(order["quantity"]),
                    price_paise=to_paise(order["price"]),
                )
            )
            result = (order.get("result") or {}).get("order_result") or {}
            if result.get("order_id"):
                order_id, fired = str(result["order_id"]), n
        product = Product(row["orders"][0]["product"])
    except (KeyError, ValueError, TypeError, IndexError):
        return None
    request = GttRequest(account_id, user_id, instrument_id, product, kind, tuple(legs))
    return GttView(
        gtt_id=str(row["id"]),
        request=request,
        status=status,
        created_at=_ts(row.get("created_at")),
        updated_at=_ts(row.get("updated_at")),
        order_id=order_id,
        fired_leg=fired,
    )


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None
