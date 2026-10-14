"""The gate: what every order, modify and cancel must pass before it reaches the broker.

Risk checks and the throttle (K3.2, K3.3) are gates. The order manager only knows
this interface, so the checks can change without touching order handling.

A gate answers with None (go ahead) or a reason that is shown to the user. It may
also wait, e.g. the throttle holding an algo's order until the rate allows it.
"""

from collections.abc import Sequence
from typing import Protocol

from mocktrade.domain.orders import OrderRequest
from mocktrade.orders.model import OrderView


class OrderGate(Protocol):
    async def admit_new(self, request: OrderRequest) -> str | None: ...

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None: ...

    async def admit_cancel(self, order: OrderView) -> str | None: ...


class OpenGate:
    """Lets everything through. For tests and tools that don't need risk checks."""

    async def admit_new(self, request: OrderRequest) -> str | None:
        return None

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        return None

    async def admit_cancel(self, order: OrderView) -> str | None:
        return None


class Gates:
    """Several gates in a row; the first refusal wins (e.g. risk checks, then throttle)."""

    def __init__(self, gates: Sequence[OrderGate]) -> None:
        self._gates = tuple(gates)

    async def admit_new(self, request: OrderRequest) -> str | None:
        for gate in self._gates:
            if (reason := await gate.admit_new(request)) is not None:
                return reason
        return None

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        for gate in self._gates:
            if (reason := await gate.admit_modify(order, new)) is not None:
                return reason
        return None

    async def admit_cancel(self, order: OrderView) -> str | None:
        for gate in self._gates:
            if (reason := await gate.admit_cancel(order)) is not None:
                return reason
        return None
