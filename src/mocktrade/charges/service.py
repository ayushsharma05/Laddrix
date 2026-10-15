"""One account's charges for the day, and estimates for orders not yet sent.

    order manager FILL events ─► ChargesService.bill (today's executed orders) ─► stream
    ticket ─► estimate(instrument, side, qty, price, product) ─► the lines it would pay

Today's charges come from MockTrade's own executed orders (the same orders the positions
are built from), so each position's net P&L subtracts exactly its own charges; they're
ready the moment a fill arrives, from any broker.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction

from mocktrade.charges.model import ChargeBill, ChargesModel, ExecutedOrder, executed, segment_of
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import Product, Side
from mocktrade.orders.model import EventKind, OrderEvent
from mocktrade.recovery import session_start

log = logging.getLogger(__name__)


class ChargesError(Exception):
    """An estimate we can't make (the reason is shown to the trader)."""


class ChargesService:
    def __init__(
        self,
        account_id: str,
        model: ChargesModel,
        orders,  # OrderManager
        instruments: Callable[[str], Instrument | None],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self.model = model
        self._orders = orders
        self._instrument = instruments
        self._clock = clock
        self._bill: ChargeBill | None = None
        self._listeners: list[Callable[[str, ChargeBill], None]] = []

    def bill(self) -> ChargeBill:
        """Today's charges so far."""
        if self._bill is None:
            self._bill = self._work_out()
        return self._bill

    def estimate(
        self, instrument_id: str, side: Side, qty: int, price_paise: int, product: Product
    ) -> ChargeBill:
        """What this order would pay if it filled in full at `price_paise`."""
        instrument = self._instrument(instrument_id)
        if instrument is None:
            raise ChargesError(f"unknown instrument {instrument_id}")
        if segment_of(instrument, product) is None:
            raise ChargesError(f"{instrument_id} can't be traded")
        if qty <= 0 or price_paise <= 0:
            raise ChargesError("an estimate needs a quantity and a price above 0")
        order = ExecutedOrder("estimate", instrument, product, side, qty,
                              Fraction(qty * price_paise))  # fmt: skip
        return self.model.bill([order])

    def subscribe(self, listener: Callable[[str, ChargeBill], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def on_order_event(self, event: OrderEvent) -> None:
        """Order manager listener: a fill changes the day's charges."""
        if event.kind is not EventKind.FILL or event.account_id != self.account_id:
            return
        bill = self._work_out()
        if bill == self._bill:
            return
        self._bill = bill
        for listener in list(self._listeners):
            try:
                listener(self.account_id, bill)
            except Exception:
                log.exception("charges listener failed")

    def _work_out(self) -> ChargeBill:
        since = session_start(self._clock())
        today = [v for v in self._orders.orders() if v.created_at >= since]
        return self.model.bill(executed(today, self._instrument))
