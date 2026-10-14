"""Translating orders between MockTrade and Kite (pure functions, no network).

Kite speaks rupees as decimals ("2501.10"), "transaction_type", "COMPLETE" and a dozen
in-between statuses; MockTrade speaks paise, Side and six statuses. Every difference
is handled here, so the adapter itself only moves messages.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from mocktrade.domain.money import PAISE_PER_RUPEE
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderUpdate

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
S = OrderStatus

# Kite's statuses (checked 14 Oct 2026; Kite says "there may be other values as well").
STATUS_MAP: dict[str, OrderStatus] = {
    "COMPLETE": S.FILLED,
    "REJECTED": S.REJECTED,
    "CANCELLED": S.CANCELLED,
    "CANCEL": S.CANCELLED,  # spelling used in postback docs
    "OPEN": S.OPEN,
    "UPDATE": S.OPEN,  # postback: modified or part-filled
    "MODIFIED": S.OPEN,
    "TRIGGER PENDING": S.OPEN,  # a stop order waiting for its trigger is working
    "MODIFY VALIDATION PENDING": S.OPEN,
    "MODIFY PENDING": S.OPEN,
    "CANCEL PENDING": S.OPEN,  # still working until the cancel is confirmed
    "PUT ORDER REQ RECEIVED": S.PENDING,
    "VALIDATION PENDING": S.PENDING,
    "OPEN PENDING": S.PENDING,
    "AMO REQ RECEIVED": S.PENDING,  # after-market order, waits for the next session
}


def to_kite_rupees(paise: int | None) -> str | None:
    """250110 -> "2501.10" (exact; never a float)."""
    if paise is None:
        return None
    return f"{Decimal(paise) / PAISE_PER_RUPEE:.2f}"


def to_paise(value: Any) -> int:
    """Kite rupees (str, Decimal, int or float) -> whole paise.

    Averages can carry fractions of a paisa (72.755): those round half up. Floats go
    through str() first, which gives the shortest exact form (2501.1 -> "2501.1")."""
    if value is None or value == "":
        return 0
    rupees = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((rupees * PAISE_PER_RUPEE).to_integral_value(rounding=ROUND_HALF_UP))


def place_params(request: OrderRequest) -> dict[str, Any]:
    """The form fields for POST /orders/:variety."""
    exchange, symbol = request.instrument_id.split(":", 1)
    params: dict[str, Any] = {
        "exchange": exchange,
        "tradingsymbol": symbol,
        "transaction_type": request.side.value,
        "quantity": request.qty,
        "product": request.product.value,
        "order_type": request.order_type.value,
        "validity": request.validity.value,
        "price": to_kite_rupees(request.price_paise),
        "trigger_price": to_kite_rupees(request.trigger_paise),
        "tag": request.client_order_id,  # echoed back on every update: how we match
    }
    if request.market_protection is not None:
        params["market_protection"] = str(request.market_protection)
    if request.algo_id is not None:
        params["algo_id"] = request.algo_id  # the exchange-issued algo id
    return {k: v for k, v in params.items() if v is not None}


def modify_params(
    qty: int | None, price_paise: int | None, trigger_paise: int | None
) -> dict[str, Any]:
    """The form fields for PUT /orders/:variety/:order_id (quantity is the new total)."""
    params = {
        "quantity": qty,
        "price": to_kite_rupees(price_paise),
        "trigger_price": to_kite_rupees(trigger_paise),
    }
    return {k: v for k, v in params.items() if v is not None}


def to_update(order: dict[str, Any]) -> OrderUpdate | None:
    """A Kite order (from GET /orders or a WebSocket "order" message) as our update.

    Returns None for a status we don't know, so a new Kite status can never break
    order handling; the next reconcile picks the order up again."""
    raw_status = str(order.get("status") or "").upper()
    status = STATUS_MAP.get(raw_status)
    if status is None:
        log.warning("unknown Kite order status %r for order %s", raw_status, order.get("order_id"))
        return None
    reason = ""
    if status in (S.REJECTED, S.CANCELLED):
        reason = order.get("status_message") or order.get("status_message_raw") or ""
        if not reason and status is S.CANCELLED:
            reason = "cancelled"
    return OrderUpdate(
        client_order_id=_tag(order),
        broker_order_id=str(order["order_id"]),
        status=status,
        filled_qty=int(order.get("filled_quantity") or 0),
        avg_price_paise=to_paise(order.get("average_price")),
        reason=reason,
        ts=_timestamp(order),
    )


def _tag(order: dict[str, Any]) -> str:
    """Our client id, from `tag` (or the newer `tags` list). Empty for outside orders."""
    tag = order.get("tag")
    if not tag:
        tags = order.get("tags") or []
        tag = tags[0] if tags else ""
    return str(tag)


def _timestamp(order: dict[str, Any]) -> datetime:
    """Kite times are India time without a zone, e.g. "2026-10-14 09:15:02"."""
    for name in ("exchange_update_timestamp", "exchange_timestamp", "order_timestamp"):
        value = order.get(name)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=IST)
        if value:
            try:
                return datetime.fromisoformat(str(value)).replace(tzinfo=IST)
            except ValueError:
                continue
    return datetime.now(UTC)


def margin_params(request: OrderRequest, variety: str = "regular") -> dict[str, Any]:
    """One order as Kite's basket-margin endpoint wants it (JSON, numbers not strings)."""
    exchange, symbol = request.instrument_id.split(":", 1)
    return {
        "exchange": exchange,
        "tradingsymbol": symbol,
        "transaction_type": request.side.value,
        "variety": variety,
        "product": request.product.value,
        "order_type": request.order_type.value,
        "quantity": request.qty,
        "price": float(to_kite_rupees(request.price_paise) or 0),
        "trigger_price": float(to_kite_rupees(request.trigger_paise) or 0),
    }
