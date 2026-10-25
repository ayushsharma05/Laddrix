"""Closing a position through the normal order path (risk, margin, throttle, audit).

Used by the MTM watch and the square-off assistant: a market order with the broker's
automatic protection for the open quantity, split so no piece is bigger than the account
allows per order (risk limits) or the exchange per order (the instrument's freeze quantity,
when known). An exit never needs margin (risk/margin.py) and risk allows closing trades even
past the daily loss limit, so these go through when it matters most.

Every closing order is marked reduce-only (#261): the risk gate refuses one that would close
more than is open, so two exits can never together reverse a position.

The exit desk (#261) coordinates a forced close of one position (instrument and product):

    lock that position (a second close of it waits, then sees what is left)
      ─► stop the algos on it (their stops would race the close; entries would reopen it)
      ─► cancel the resting orders that would also close it, and wait briefly for them
         (a fill meanwhile is fine: it is counted in the position)
      ─► read the position now; take off what closing orders still working will close
         (a cancel that didn't take, an order whose fate is unknown)
      ─► send closing orders for the rest only, and report what it couldn't close
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.orders.model import OrderError, OrderView, Pending

log = logging.getLogger(__name__)

SETTLE_S = 3.0  # how long the desk waits for the cancels it sent before closing the rest
POLL_S = 0.05


async def close_position(
    orders,  # OrderManager
    instrument: Instrument,
    product: Product,
    qty: int,  # the position: + long (sell to close), − short (buy to close)
    *,
    user_id: str,
    max_lots: int,
    basket_id: str,  # one id for the whole close: its pieces are meant to be identical
) -> list[OrderView]:
    if qty == 0:
        return []
    side = Side.SELL if qty > 0 else Side.BUY
    left = abs(qty)
    piece = max(instrument.lot_size, max_lots * instrument.lot_size)
    freeze = instrument.max_order_qty  # the exchange's most per order (#424)
    if freeze:
        piece = min(piece, freeze)
    sent = []
    while left > 0:
        size = min(left, piece)
        request = OrderRequest("", orders.account_id, user_id, instrument.id, side, size,
                               OrderType.MARKET, product, market_protection=Decimal(-1),
                               basket_id=basket_id, reduce_only=True,
                               forced_close=True)  # fmt: skip
        sent.append(await orders.submit(request))
        left -= size
    return sent


class StopsAlgos(Protocol):
    """The algo engine, as an exit sees it: it stops the algos working a position (#261)."""

    def stop_on(self, instrument_id: str, product: Product, reason: str) -> list[str]: ...


@dataclass
class CloseResult:
    """What a forced close of one position did, for the audit trail and the screens."""

    instrument_id: str
    product: Product
    position: int = 0  # what was open when the desk looked, after the cancels settled
    sent: list[OrderView] = field(default_factory=list)
    stopped_algos: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)  # resting closing orders it cancelled
    left_open: int = 0  # not closed by this desk: other closing orders still own it
    notes: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [o.client_order_id for o in self.sent]

    @property
    def refused(self) -> list[str]:
        return [o.reason for o in self.sent if o.status.value == "REJECTED"]

    def words(self) -> str:
        """One line for the audit trail."""
        what = f"closing {self.position:+d} {self.instrument_id} {self.product.value}"
        parts = []
        if self.ids:
            parts.append(f"sent {', '.join(self.ids)}")
        if self.stopped_algos:
            parts.append(f"stopped algos {', '.join(self.stopped_algos)}")
        if self.cancelled:
            parts.append(f"cancelled {', '.join(self.cancelled)}")
        if self.left_open:
            parts.append(f"{self.left_open} left to orders already closing it")
        parts += self.notes
        if self.refused:
            parts.append(f"refused: {'; '.join(self.refused)}")
        return f"{what}: {'; '.join(parts) or 'nothing left to close'}"


def position_of(book) -> Callable[[str, Product], int]:
    """The position book's quantity for (instrument, product), 0 when there is none."""

    def held(instrument_id: str, product: Product) -> int:
        view = book.get(instrument_id, product)
        return 0 if view is None else view.qty

    return held


class ExitDesk:
    """One per account. Every forced close goes through it (see the module notes)."""

    def __init__(
        self,
        orders,  # OrderManager
        position: Callable[[str, Product], int],  # (instrument, product) -> qty, + long
        *,
        algos: Callable[[], StopsAlgos | None] = lambda: None,  # the AlgoEngine, once built
        settle_s: float = SETTLE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._orders = orders
        self._position = position
        self._algos = algos
        self.settle_s = settle_s
        self._sleep = sleep
        self._locks: dict[tuple[str, Product], asyncio.Lock] = {}

    async def close(
        self,
        instrument: Instrument,
        product: Product,
        *,
        user_id: str,
        max_lots: int,
        basket_id: str,
        why: str,
    ) -> CloseResult:
        key = (instrument.id, product)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._close(instrument, product, user_id=user_id, max_lots=max_lots,
                                     basket_id=basket_id, why=why)  # fmt: skip

    async def _close(self, instrument, product, *, user_id, max_lots, basket_id, why):
        result = CloseResult(instrument.id, product)
        engine = self._algos()
        if engine is not None:
            result.stopped_algos = engine.stop_on(instrument.id, product, f"forced close: {why}")
        held = self._position(instrument.id, product)
        if held == 0:
            return result
        side = Side.SELL if held > 0 else Side.BUY
        for view in self._closing(instrument.id, product, side):
            if view.pending is Pending.UNCONFIRMED:
                continue  # its fate is unknown, so it can't be cancelled: it stays counted
            try:
                await self._orders.cancel(view.client_order_id)
                result.cancelled.append(view.client_order_id)
            except OrderError as exc:
                result.notes.append(f"couldn't cancel {view.client_order_id}: {exc.reason}")
        waited = 0.0
        while waited < self.settle_s and any(
            v.client_order_id in result.cancelled
            for v in self._closing(instrument.id, product, side)
        ):
            await self._sleep(POLL_S)
            waited += POLL_S
        held = self._position(instrument.id, product)  # fills meanwhile are in it
        result.position = held
        if held == 0:
            return result
        side = Side.SELL if held > 0 else Side.BUY
        working = sum(v.leaves_qty for v in self._closing(instrument.id, product, side))
        result.left_open = min(abs(held), working)
        rest = abs(held) - result.left_open
        if result.left_open:
            log.warning("exit desk %s %s: %d still owned by working closing orders",
                        instrument.id, product.value, result.left_open)  # fmt: skip
        if rest:
            signed = rest if held > 0 else -rest
            result.sent = await close_position(self._orders, instrument, product, signed,
                                               user_id=user_id, max_lots=max_lots,
                                               basket_id=basket_id)  # fmt: skip
        return result

    def _closing(self, instrument_id: str, product: Product, side: Side) -> list[OrderView]:
        """Working orders on this position that would reduce it (any order on the closing
        side does, marked reduce-only or not)."""
        return [
            v
            for v in self._orders.working()
            if v.request.instrument_id == instrument_id and v.request.product is product
            and v.request.side is side and v.leaves_qty > 0
        ]  # fmt: skip
