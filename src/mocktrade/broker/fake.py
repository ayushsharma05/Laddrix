"""A scripted broker for tests: records what it was asked and replays what a test tells it."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from mocktrade.broker.base import BrokerError, BrokerPosition
from mocktrade.domain.orders import OrderRequest, OrderUpdate


@dataclass
class FakeBroker:
    name: str = "fake"
    placed: list[OrderRequest] = field(default_factory=list)
    modified: list[tuple[str, dict]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    broker_orders: list[OrderUpdate] = field(default_factory=list)
    broker_positions: list[BrokerPosition] = field(default_factory=list)
    _queue: asyncio.Queue[OrderUpdate] = field(default_factory=asyncio.Queue)
    _next_id: int = 1
    _fail_next: str | None = None

    async def place(self, request: OrderRequest) -> str:
        self._raise_if_failing()
        self.placed.append(request)
        broker_order_id = f"F{self._next_id}"
        self._next_id += 1
        return broker_order_id

    async def modify(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        price_paise: int | None = None,
        trigger_paise: int | None = None,
    ) -> None:
        self._raise_if_failing()
        changes = {"qty": qty, "price_paise": price_paise, "trigger_paise": trigger_paise}
        self.modified.append((broker_order_id, changes))

    async def cancel(self, broker_order_id: str) -> None:
        self._raise_if_failing()
        self.cancelled.append(broker_order_id)

    async def updates(self) -> AsyncIterator[OrderUpdate]:
        while True:
            yield await self._queue.get()

    async def orders(self) -> list[OrderUpdate]:
        return list(self.broker_orders)

    async def positions(self) -> list[BrokerPosition]:
        return list(self.broker_positions)

    # --- test controls -----------------------------------------------------------------

    def push(self, update: OrderUpdate) -> None:
        """Make the broker report this update."""
        self._queue.put_nowait(update)

    def fail_next(self, reason: str) -> None:
        """Make the next request raise BrokerError(reason)."""
        self._fail_next = reason

    def _raise_if_failing(self) -> None:
        if self._fail_next is not None:
            reason, self._fail_next = self._fail_next, None
            raise BrokerError(reason)
