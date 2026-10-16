"""What the order manager tells the rest of the app: order views, fills and events.

Everything here is frozen (read-only). Screens, the event log and positions get
snapshots, so nothing outside the order manager can change an order by accident.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from mocktrade.domain.orders import CLIENT_ORDER_ID, OrderRequest, OrderStatus


class Pending(Enum):
    """A request we've sent about a working order that the broker hasn't settled yet."""

    NONE = "NONE"
    UNCONFIRMED = "UNCONFIRMED"  # place got no clear answer: we don't know if the broker has it
    CANCEL = "CANCEL"  # cancel asked for, not yet confirmed
    MODIFY = "MODIFY"  # a change sent and not yet confirmed: risk counts the larger quantity


@dataclass(frozen=True)
class OrderView:
    """One order as screens show it. `request` holds the current (possibly modified) values."""

    request: OrderRequest
    broker_order_id: str | None
    status: OrderStatus
    filled_qty: int
    avg_price_paise: int
    reason: str  # why it was rejected or cancelled; empty otherwise
    pending: Pending
    created_at: datetime
    updated_at: datetime

    @property
    def client_order_id(self) -> str:
        return self.request.client_order_id

    @property
    def leaves_qty(self) -> int:
        """Quantity still working (0 once the order is finished)."""
        return 0 if self.status.is_final else self.request.qty - self.filled_qty

    @property
    def is_working(self) -> bool:
        return not self.status.is_final


@dataclass(frozen=True)
class Fill:
    """New quantity traded on one order. Positions are built from these."""

    client_order_id: str
    qty: int
    price_paise: int
    exact: bool  # False when the price was worked out from the average (broker gave no fill price)


class EventKind(Enum):
    SUBMITTED = "SUBMITTED"  # accepted by us, about to pass the gate
    REJECTED = "REJECTED"  # refused by our rules, risk, throttle or the broker
    SENT = "SENT"  # the broker took it and gave its order id
    UNCONFIRMED = "UNCONFIRMED"  # no answer from the broker: reconciliation will settle it
    UPDATED = "UPDATED"  # the broker reported a new state
    FILL = "FILL"  # new quantity traded (comes with an UPDATED event)
    MODIFY_SENT = "MODIFY_SENT"
    MODIFY_UNCONFIRMED = "MODIFY_UNCONFIRMED"  # no answer to a change: the broker's terms decide
    CANCEL_SENT = "CANCEL_SENT"
    CANCELLED_LOCALLY = "CANCELLED_LOCALLY"  # cancelled before it was ever sent
    IGNORED = "IGNORED"  # a duplicate, stale or impossible update: kept for the audit trail
    EXTERNAL = "EXTERNAL"  # an order we didn't place (e.g. from the broker's own app)


@dataclass(frozen=True)
class OrderEvent:
    kind: EventKind
    ts: datetime
    account_id: str
    client_order_id: str
    order: OrderView | None  # None only for EXTERNAL events
    detail: str = ""
    fill: Fill | None = None


class OrderError(Exception):
    """A modify or cancel we can't do. `reason` is safe to show the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ClientIds:
    """Makes our order ids: a prefix plus a counter, e.g. MT261014 + 42 -> MT26101442.

    Use a different prefix each day (the date) so ids never repeat across days; the
    event log restores `start` after a restart so they never repeat within a day.
    """

    def __init__(self, prefix: str, start: int = 0) -> None:
        if not CLIENT_ORDER_ID.fullmatch(prefix) or len(prefix) > 12:
            raise ValueError("prefix must be 1-12 letters or digits (ids are at most 20)")
        self._prefix = prefix
        self._last = start

    def next(self) -> str:
        self._last += 1
        client_order_id = f"{self._prefix}{self._last}"
        if len(client_order_id) > 20:
            raise RuntimeError("client order id counter exhausted for this prefix")
        return client_order_id

    @property
    def last(self) -> int:
        return self._last
