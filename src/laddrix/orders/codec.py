"""Order views to and from plain JSON-ready dicts, for the event log and the API.

Written field by field on purpose (not `asdict`): the stored format is a contract.
Renaming a Python field must not silently change what's on disk, and old records
must stay readable. Bump SCHEMA when the format changes, and keep reading old ones.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side, Validity
from laddrix.orders.model import Fill, OrderView, Pending

SCHEMA = 1


def request_to_dict(r: OrderRequest) -> dict[str, Any]:
    return {
        "client_order_id": r.client_order_id,
        "account_id": r.account_id,
        "user_id": r.user_id,
        "instrument_id": r.instrument_id,
        "side": r.side.value,
        "qty": r.qty,
        "order_type": r.order_type.value,
        "product": r.product.value,
        "validity": r.validity.value,
        "price_paise": r.price_paise,
        "trigger_paise": r.trigger_paise,
        "market_protection": None if r.market_protection is None else str(r.market_protection),
        "algo_id": r.algo_id,
        "parent_id": r.parent_id,
        "basket_id": r.basket_id,
        "reduce_only": r.reduce_only,
        "forced_close": r.forced_close,
        "algo_role": r.algo_role,
    }


def request_from_dict(d: dict[str, Any]) -> OrderRequest:
    protection = d.get("market_protection")
    return OrderRequest(
        client_order_id=d["client_order_id"],
        account_id=d["account_id"],
        user_id=d["user_id"],
        instrument_id=d["instrument_id"],
        side=Side(d["side"]),
        qty=d["qty"],
        order_type=OrderType(d["order_type"]),
        product=Product(d["product"]),
        validity=Validity(d.get("validity", Validity.DAY.value)),
        price_paise=d.get("price_paise"),
        trigger_paise=d.get("trigger_paise"),
        market_protection=None if protection is None else Decimal(protection),
        algo_id=d.get("algo_id"),
        parent_id=d.get("parent_id"),
        basket_id=d.get("basket_id"),  # absent in orders stored before baskets
        reduce_only=bool(d.get("reduce_only", False)),  # absent before #261
        forced_close=bool(d.get("forced_close", False)),  # absent before #262
        algo_role=str(d.get("algo_role", "")),  # absent before #427
    )


def view_to_dict(v: OrderView) -> dict[str, Any]:
    return {
        "request": request_to_dict(v.request),
        "broker_order_id": v.broker_order_id,
        "status": v.status.value,
        "filled_qty": v.filled_qty,
        "avg_price_paise": v.avg_price_paise,
        "reason": v.reason,
        "pending": v.pending.value,
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat(),
        "filled_value_paise": v.filled_value_paise,
    }


def view_from_dict(d: dict[str, Any]) -> OrderView:
    return OrderView(
        request=request_from_dict(d["request"]),
        broker_order_id=d.get("broker_order_id"),
        status=OrderStatus(d["status"]),
        filled_qty=d["filled_qty"],
        avg_price_paise=d["avg_price_paise"],
        reason=d.get("reason", ""),
        pending=Pending(d.get("pending", Pending.NONE.value)),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        # saved before #275: the rounded average is the best there is
        filled_value_paise=d.get("filled_value_paise", d["filled_qty"] * d["avg_price_paise"]),
    )


def fill_to_dict(f: Fill) -> dict[str, Any]:
    return {
        "client_order_id": f.client_order_id,
        "qty": f.qty,
        "price_paise": f.price_paise,
        "exact": f.exact,
        "value_paise": f.value_paise,
    }


def fill_from_dict(d: dict[str, Any]) -> Fill:
    return Fill(d["client_order_id"], d["qty"], d["price_paise"], d["exact"], d.get("value_paise"))
