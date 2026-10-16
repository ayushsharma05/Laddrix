"""The gate: what every order, modify and cancel must pass before it reaches the broker.

Risk checks and the throttle (K3.2, K3.3) are gates. The order manager only knows
this interface, so the checks can change without touching order handling.

A gate answers with None (go ahead) or a reason that is shown to the user. It may
also wait, e.g. the throttle holding an algo's order until the rate allows it.
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from mocktrade.domain.orders import OrderRequest
from mocktrade.orders.model import OrderView

WAITED_S = 0.05  # a gate that took this long made the order wait (the throttle's pauses)


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
    """Several gates in a row; the first refusal wins (e.g. risk checks, then throttle).

    A gate may make an order wait (the throttle holds an algo's order for its turn). The
    world can change meanwhile (an admin tightens a limit, someone presses STOP), so after a
    wait the gates marked `recheck_after_wait` that came before it run again (#260)."""

    def __init__(
        self, gates: Sequence[OrderGate], *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._gates = tuple(gates)
        self._now = monotonic

    async def admit_new(self, request: OrderRequest) -> str | None:
        return await self._through(lambda g: g.admit_new(request))

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        return await self._through(lambda g: g.admit_modify(order, new))

    async def _through(self, ask: Callable[[OrderGate], Awaitable[str | None]]) -> str | None:
        for n, gate in enumerate(self._gates):
            started = self._now()
            if (reason := await ask(gate)) is not None:
                return reason
            if self._now() - started >= WAITED_S:  # it waited: look again at what came before
                for earlier in self._gates[:n]:
                    if getattr(earlier, "recheck_after_wait", False):
                        if (reason := await ask(earlier)) is not None:
                            return f"{reason} (checked again after waiting for the order rate)"
        return None

    async def admit_cancel(self, order: OrderView) -> str | None:
        for gate in self._gates:
            if (reason := await gate.admit_cancel(order)) is not None:
                return reason
        return None
