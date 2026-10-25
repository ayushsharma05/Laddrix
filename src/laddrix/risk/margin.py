"""The margin gate: refuse an order the account's funds can't carry, before it is sent.

    order ─► an exit? (it only reduces the position) ─► always allowed, never asked about
          ─► broker: what would it block? (MarginQuoter: Kite /margins/basket with the
             positions considered; paper: PaperMargins, an estimate)
          ─► funds: what's available? (FundsSource via the portfolio service, cached)
          ─► needs more than available ─► refused, saying both numbers
          ─► no answer (error, timeout, funds unknown) ─► ALLOWED, with a warning:
             a margin outage must never block trading, least of all getting out

The broker checks margin again when the order arrives (Kite rejects what it can't fund),
so this gate is a courtesy that saves a round trip and says why in plain numbers. It is
optional per account and costs one broker request for orders that add exposure (bounded by
`timeout_s`); exits cost nothing.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from laddrix.broker.base import BrokerError, MarginQuoter
from laddrix.domain.money import format_rupees as rupees
from laddrix.domain.orders import OrderRequest
from laddrix.orders.model import OrderView

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarginCheck:
    exit: bool  # only reduces the position: never checked
    required_paise: int | None  # what the broker says this order adds (None: no answer)
    available_paise: int | None  # what the funds say is free (None: unknown)
    note: str = ""  # why something is unknown

    @property
    def enough(self) -> bool | None:
        """None when it can't be told (the gate then lets it through)."""
        if self.exit:
            return True
        if self.required_paise is None or self.available_paise is None:
            return None
        return self.required_paise <= self.available_paise


class MarginGate:
    def __init__(
        self,
        quoter: object,  # the broker: used when it is a MarginQuoter
        available: Callable[[], Awaitable[int | None]],
        position: Callable[[str], int],  # net quantity now (+ long, − short)
        *,
        timeout_s: float = 1.0,
        on_unchecked: Callable[[OrderRequest, str], None] | None = None,
    ) -> None:
        self._quoter = quoter if isinstance(quoter, MarginQuoter) else None
        self._available = available
        self._position = position
        self.timeout_s = timeout_s
        self._on_unchecked = on_unchecked

    async def check(self, request: OrderRequest) -> MarginCheck:
        net = self._position(request.instrument_id)
        if net and (net > 0) != (request.side.sign > 0) and request.qty <= abs(net):
            return MarginCheck(True, 0, None)
        if self._quoter is None:
            return MarginCheck(False, None, None, "this broker doesn't quote margins")
        try:
            margin = await asyncio.wait_for(self._quoter.basket_margin([request]), self.timeout_s)
            required = margin.final_paise
        except BrokerError as exc:
            return MarginCheck(False, None, None, f"the broker couldn't quote it: {exc.reason}")
        except TimeoutError:
            return MarginCheck(False, None, None, f"no margin quote within {self.timeout_s:g} s")
        except Exception as exc:  # a read: nothing was placed
            log.warning("margin quote failed: %r", exc)
            return MarginCheck(False, None, None, "no margin quote from the broker")
        try:
            available = await asyncio.wait_for(self._available(), self.timeout_s)
        except Exception as exc:
            log.warning("funds for the margin check: %r", exc)
            available = None
        note = "" if available is not None else "available funds unknown"
        return MarginCheck(False, required, available, note)

    # --- the gate (orders/gate.py) -----------------------------------------------------------

    async def admit_new(self, request: OrderRequest) -> str | None:
        result = await self.check(request)
        return self._verdict(request, result)

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        """Only a bigger order can need more margin: the increase is checked like a new one."""
        more = new.qty - order.request.qty
        if more <= 0:
            return None
        increase = replace(new, qty=more)
        return self._verdict(increase, await self.check(increase))

    async def admit_cancel(self, order: OrderView) -> str | None:
        return None  # getting out is always allowed

    def _verdict(self, request: OrderRequest, result: MarginCheck) -> str | None:
        enough = result.enough
        if enough is None:
            if self._on_unchecked is not None:
                self._on_unchecked(request, result.note)
            return None  # fail open: say so, let it through
        if enough:
            return None
        assert result.required_paise is not None and result.available_paise is not None
        return (
            f"not enough margin: this order needs {rupees(result.required_paise)}, "
            f"{rupees(result.available_paise)} is available"
        )
