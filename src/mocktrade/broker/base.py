"""The broker interface. Kite, Paper, a test Fake and future brokers all follow it.

One Broker object serves one broker account. Everything above this interface
(order manager, risk, algos, screens) is written against it and never imports
a specific broker, which is what keeps MockTrade broker-agnostic.

Guarantees every implementation must keep:
- `place` returns quickly with the broker's order id, or raises BrokerError.
- BrokerError means a clear refusal: the broker did NOT act. Any other exception
  (timeout, dropped connection) means the outcome is unknown; callers then check
  the broker's order list instead of assuming anything.
- Every placed order eventually reaches a final status (filled, cancelled or
  rejected) through `updates()`.
- Updates for one order arrive in the order they happened.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mocktrade.domain.gtt import GttRequest, GttSpec, GttView
from mocktrade.domain.instruments import Instrument, InstrumentId
from mocktrade.domain.orders import OrderRequest, OrderUpdate, Product


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
    overnight_qty: int = 0  # carried in from earlier days (part of qty)
    close_paise: int = 0  # the previous close, for valuing what was carried in
    product: Product | None = None


@dataclass(frozen=True)
class BasketMargin:
    """What the broker would block for a set of orders placed together."""

    initial_paise: int  # each leg on its own, added up
    final_paise: int  # with the hedge benefit: what is actually blocked


@runtime_checkable
class MarginQuoter(Protocol):
    """A broker that can price a basket's margin before it is sent (optional)."""

    async def basket_margin(self, requests: list[OrderRequest]) -> BasketMargin: ...


@runtime_checkable
class GttBook(Protocol):
    """A broker that holds GTT (good till triggered) orders for us (optional). Placing,
    changing and deleting raise BrokerError with the broker's reason when refused."""

    def gtt_spec(self, instrument: Instrument) -> GttSpec: ...

    async def place_gtt(self, request: GttRequest, ltp_paise: int) -> str: ...

    async def modify_gtt(self, gtt_id: str, request: GttRequest, ltp_paise: int) -> None: ...

    async def delete_gtt(self, gtt_id: str) -> None: ...

    async def gtts(self) -> list[GttView]:
        """Every GTT the broker holds for this account, with its status."""
        ...


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
