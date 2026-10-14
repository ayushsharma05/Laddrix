"""The broker interface. Kite, Paper, a test Fake and future brokers all follow it.

One Broker object serves one broker account. Everything above this interface
(order manager, risk, algos, screens) is written against it and never imports
a specific broker, which is what keeps MockTrade broker-agnostic.

Guarantees every implementation must keep:
- `place` returns quickly with the broker's order id, or raises BrokerError.
- Every placed order eventually reaches a final status (filled, cancelled or
  rejected) through `updates()`.
- Updates for one order arrive in the order they happened.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.orders import OrderRequest, OrderUpdate


class BrokerError(Exception):
    """The broker refused or failed a request. `reason` is safe to show the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class BrokerPosition:
    """The broker's view of one position, used to reconcile ours."""

    instrument_id: InstrumentId
    qty: int  # positive long, negative short
    avg_price_paise: int


@runtime_checkable
class Broker(Protocol):
    """Everything MockTrade needs from a broker, for one account."""

    name: str

    async def place(self, request: OrderRequest) -> str:
        """Send a new order. Returns the broker's order id."""
        ...

    async def modify(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        price_paise: int | None = None,
        trigger_paise: int | None = None,
    ) -> None:
        """Change a working order. Quantity is the new total, as at Indian brokers."""
        ...

    async def cancel(self, broker_order_id: str) -> None:
        """Ask to cancel a working order. The cancellation arrives as an update."""
        ...

    def updates(self) -> AsyncIterator[OrderUpdate]:
        """A never-ending stream of order updates for this account."""
        ...

    async def orders(self) -> list[OrderUpdate]:
        """The broker's current view of today's orders (for reconciliation)."""
        ...

    async def positions(self) -> list[BrokerPosition]:
        """The broker's current view of positions (for reconciliation)."""
        ...
