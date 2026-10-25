"""Translating orders between Laddrix and Kite (pure functions, no network).

Kite speaks rupees as decimals ("2501.10"), "transaction_type", "COMPLETE" and a dozen
in-between statuses; Laddrix speaks paise, Side and six statuses. Every difference
is handled here, so the adapter itself only moves messages.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, TypedDict, overload

from laddrix.domain.money import PAISE_PER_RUPEE
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderUpdate

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


@overload
def to_kite_rupees(paise: int) -> str: ...
@overload
def to_kite_rupees(paise: None) -> None: ...
def to_kite_rupees(paise: int | None) -> str | None:
    """250110 -> "2501.10" (exact; never a float). None stays None."""
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


def exact_paise(value: Any) -> Fraction | None:
    """Kite rupees -> paise exactly, fractions of a paisa kept (72.755 -> 7275.5): an order's
    traded value is worked out from this (#275). None when there is none."""
    if value is None or value == "":
        return None
    rupees = value if isinstance(value, Decimal) else Decimal(str(value))
    return Fraction(rupees) * PAISE_PER_RUPEE


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
        # the exchange-issued algo id. Kite's public docs name no field for it (checked 23 Oct
        # 2026): confirm the name with Zerodha before an id is set (docs/design/compliance.md)
        params["algo_id"] = request.algo_id
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


def to_update(order: dict[str, Any]) -> OrderUpdate:
    """A Kite order (from GET /orders or a WebSocket "order" message) as our update.

    A status we don't know is never dropped: dropping it would make the order look missing,
    and an unanswered order of ours missing from the list counts towards rejecting it (#259).
    It comes back as PENDING with the raw status in `reason`: proof the order exists, which
    can't move a known order backwards (the manager ignores that, and records it)."""
    raw_status = str(order.get("status") or "").upper()
    status = STATUS_MAP.get(raw_status)
    if status is None:
        log.warning("unknown Kite order status %r for order %s", raw_status, order.get("order_id"))
        return OrderUpdate(
            client_order_id=_tag(order),
            broker_order_id=str(order["order_id"]),
            status=S.PENDING,
            filled_qty=int(order.get("filled_quantity") or 0),
            avg_price_paise=to_paise(order.get("average_price")),
            avg_price_exact=exact_paise(order.get("average_price")),
            reason=f"the broker reports a status Laddrix doesn't know: {raw_status or '(none)'}",
            ts=_timestamp(order),
            **_terms(order),
        )
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
        avg_price_exact=exact_paise(order.get("average_price")),
        reason=reason,
        ts=_timestamp(order),
        **_terms(order),
    )


class _Terms(TypedDict):
    qty: int | None
    price_paise: int | None
    trigger_paise: int | None


def _terms(order: dict[str, Any]) -> _Terms:
    """The order's terms as Kite has them now (they settle a change with no answer, #260).
    A price or trigger of 0 means none (a market order); a missing field says nothing."""
    qty = order.get("quantity")
    price = to_paise(order.get("price")) if order.get("price") is not None else None
    trigger = (
        to_paise(order.get("trigger_price")) if order.get("trigger_price") is not None else None
    )
    return {
        "qty": int(qty) if qty not in (None, "") else None,
        "price_paise": price or None,
        "trigger_paise": trigger or None,
    }


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
