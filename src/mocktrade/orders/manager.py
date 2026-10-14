"""The order manager: one place that knows every order of one broker account.

    submit ─► check rules ─► gate (risk, throttle) ─► broker.place ─► SENT
                  │                 │                     │
               REJECTED          REJECTED       BrokerError: REJECTED
                                                 no answer: UNCONFIRMED (reconcile settles it)

    broker updates ─► match (client id, else broker id) ─► only move forward ─► events

Rules it keeps:
- Status only moves forward (see ALLOWED). Filled quantity never shrinks. Duplicate,
  stale and impossible updates are ignored but still reported, for the audit trail.
- Updates are matched by our client id first (Kite echoes it as the order tag), so an
  update that arrives before `place` returns is not lost. Updates naming only an
  unknown broker id wait until that id is known, then replay.
- `submit` with an id we already have returns that order instead of placing twice.
- A broker's BrokerError is a clear refusal. Any other failure (timeout, dropped
  connection) means we don't know the outcome, so the order is never assumed
  rejected: it stays PENDING, marked UNCONFIRMED, until the broker's order list
  settles it (`reconcile`).
- Cancel before the broker's id is known is remembered and sent as soon as it is.

One manager per broker account (the "sharding" in the architecture): accounts never
share state, so they can later run in separate processes.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderUpdate
from mocktrade.orders.gate import OpenGate, OrderGate
from mocktrade.orders.model import (
    ClientIds,
    EventKind,
    Fill,
    OrderError,
    OrderEvent,
    OrderView,
    Pending,
)

log = logging.getLogger(__name__)

S = OrderStatus
ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    S.PENDING: frozenset({S.OPEN, S.PARTIAL, S.FILLED, S.CANCELLED, S.REJECTED}),
    S.OPEN: frozenset({S.PARTIAL, S.FILLED, S.CANCELLED, S.REJECTED}),
    S.PARTIAL: frozenset({S.FILLED, S.CANCELLED}),
    S.FILLED: frozenset(),
    S.CANCELLED: frozenset(),
    S.REJECTED: frozenset(),
}

Listener = Callable[[OrderEvent], None]
MAX_PARKED = 1000  # unmatched broker ids we remember before dropping the oldest


@dataclass
class _Order:
    request: OrderRequest
    created_at: datetime
    updated_at: datetime
    broker_order_id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_price_paise: int = 0
    reason: str = ""
    pending: Pending = Pending.NONE
    sent: bool = False  # has `place` been called?
    cancel_requested: bool = False
    cancel_sent: bool = False  # guards against sending the same cancel twice

    def view(self) -> OrderView:
        return OrderView(
            request=self.request,
            broker_order_id=self.broker_order_id,
            status=self.status,
            filled_qty=self.filled_qty,
            avg_price_paise=self.avg_price_paise,
            reason=self.reason,
            pending=self.pending,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class OrderManager:
    def __init__(
        self,
        account_id: str,
        broker: Broker,
        instruments: Callable[[str], Instrument | None],
        *,
        ids: ClientIds,
        gate: OrderGate | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self._broker = broker
        self._instrument = instruments
        self._ids = ids
        self._gate = gate or OpenGate()
        self._clock = clock
        self._orders: dict[str, _Order] = {}  # client id -> order, in arrival order
        self._by_broker_id: dict[str, _Order] = {}
        self._parked: dict[str, list[OrderUpdate]] = {}  # updates for broker ids not yet known
        self._external: dict[str, OrderStatus] = {}  # orders we didn't place, last status seen
        self._listeners: list[Listener] = []
        self._tasks: set[asyncio.Task] = set()

    # --- reading --------------------------------------------------------------------------

    def get(self, client_order_id: str) -> OrderView | None:
        order = self._orders.get(client_order_id)
        return order.view() if order else None

    def by_broker_id(self, broker_order_id: str) -> OrderView | None:
        order = self._by_broker_id.get(broker_order_id)
        return order.view() if order else None

    def orders(self) -> list[OrderView]:
        return [o.view() for o in self._orders.values()]

    def working(self) -> list[OrderView]:
        return [o.view() for o in self._orders.values() if not o.status.is_final]

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call `listener` with every event from now on. Returns a function that stops it."""
        self._listeners.append(listener)

        def stop() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return stop

    # --- new orders -----------------------------------------------------------------------

    async def submit(self, request: OrderRequest) -> OrderView:
        """Send a new order. Leave `client_order_id` empty to get a fresh one.

        Never raises for a refused order: the returned view says REJECTED and why, and
        the refusal is in the audit trail like everything else.
        """
        if not request.client_order_id:
            request = replace(request, client_order_id=self._ids.next())
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing.view()  # a retry of an order we already have: never place twice

        now = self._clock()
        order = _Order(request=request, created_at=now, updated_at=now)
        self._orders[request.client_order_id] = order
        self._emit(EventKind.SUBMITTED, order)

        reason = self._rule_problem(request)
        if reason is None:
            reason = await self._gate.admit_new(request)
        if reason is not None:
            self._reject_locally(order, reason)
            return order.view()
        if order.cancel_requested:  # cancelled while waiting at the gate
            self._finish_locally(order)
            return order.view()

        order.sent = True
        try:
            broker_order_id = await self._broker.place(request)
        except BrokerError as exc:
            self._reject_locally(order, exc.reason)
            return order.view()
        except asyncio.CancelledError:
            self._mark_unconfirmed(order, "stopped while waiting for the broker")
            raise
        except Exception as exc:  # no clear answer: never guess, reconcile will tell
            log.warning("place %s: no clear answer: %r", request.client_order_id, exc)
            self._mark_unconfirmed(order, f"no answer from broker: {exc!r}")
            return order.view()

        self._register_broker_id(order, broker_order_id)
        self._touch(order)
        self._emit(EventKind.SENT, order)
        self._replay_parked(broker_order_id)
        await self._send_cancel_if_requested(order)
        return order.view()

    def _rule_problem(self, request: OrderRequest) -> str | None:
        if request.account_id != self.account_id:
            return f"order is for account {request.account_id}, not {self.account_id}"
        instrument = self._instrument(request.instrument_id)
        if instrument is None:
            return f"unknown instrument {request.instrument_id}"
        try:
            request.validate(instrument)
        except ValueError as exc:
            return str(exc)
        return None

    # --- changing and cancelling ------------------------------------------------------------

    async def modify(
        self,
        client_order_id: str,
        *,
        qty: int | None = None,
        price_paise: int | None = None,
        trigger_paise: int | None = None,
    ) -> OrderView:
        """Change a working order. Raises OrderError with a readable reason if it can't."""
        order = self._working_order(client_order_id)
        if order.broker_order_id is None:
            raise OrderError("the broker hasn't acknowledged this order yet: try again shortly")
        if order.pending is Pending.CANCEL:
            raise OrderError("a cancel is already on its way for this order")
        if qty is None and price_paise is None and trigger_paise is None:
            raise OrderError("nothing to change")
        new = order.request
        if qty is not None:
            if qty <= order.filled_qty:
                raise OrderError(f"new quantity {qty} must be above the {order.filled_qty} filled")
            new = replace(new, qty=qty)
        if price_paise is not None:
            new = replace(new, price_paise=price_paise)
        if trigger_paise is not None:
            new = replace(new, trigger_paise=trigger_paise)
        if (problem := self._rule_problem(new)) is not None:
            raise OrderError(problem)
        if (reason := await self._gate.admit_modify(order.view(), new)) is not None:
            raise OrderError(reason)
        try:
            await self._broker.modify(
                order.broker_order_id, qty=qty, price_paise=price_paise, trigger_paise=trigger_paise
            )
        except BrokerError as exc:
            raise OrderError(exc.reason) from None
        except Exception as exc:
            log.warning("modify %s: no clear answer: %r", client_order_id, exc)
            raise OrderError("no answer from the broker: check the order before retrying") from None
        if order.status.is_final:  # finished while the modify was on its way
            return order.view()
        order.request = new
        self._touch(order)
        self._emit(
            EventKind.MODIFY_SENT, order, detail=_describe_changes(qty, price_paise, trigger_paise)
        )
        return order.view()

    async def cancel(self, client_order_id: str) -> OrderView:
        """Ask to cancel a working order. The CANCELLED status arrives as a broker update."""
        order = self._working_order(client_order_id)
        if order.cancel_requested:
            return order.view()  # already asked: a second click changes nothing
        if (reason := await self._gate.admit_cancel(order.view())) is not None:
            raise OrderError(reason)
        order.cancel_requested = True
        if not order.sent:
            return order.view()  # still at the gate: `submit` will finish it locally
        if order.broker_order_id is None:
            order.pending = Pending.CANCEL  # sent as soon as the broker's id arrives
            self._touch(order)
            return order.view()
        await self._send_cancel(order)
        return order.view()

    async def cancel_all(self) -> list[tuple[str, str]]:
        """Cancel every working order (used by the kill switch). Returns (client id, reason)
        for each cancel that failed; the others are on their way."""
        failures = []
        for view in self.working():
            try:
                await self.cancel(view.client_order_id)
            except OrderError as exc:
                failures.append((view.client_order_id, exc.reason))
        return failures

    async def _send_cancel_if_requested(self, order: _Order) -> None:
        if order.cancel_requested and not order.cancel_sent and not order.status.is_final:
            try:
                await self._send_cancel(order)
            except OrderError as exc:
                log.warning("deferred cancel of %s failed: %s", order.request.client_order_id, exc)

    async def _send_cancel(self, order: _Order) -> None:
        order.pending = Pending.CANCEL
        order.cancel_sent = True
        try:
            await self._broker.cancel(order.broker_order_id)
        except Exception as exc:
            if not order.status.is_final:
                order.pending = Pending.NONE
            order.cancel_requested = order.cancel_sent = False
            self._touch(order)
            if isinstance(exc, BrokerError):
                raise OrderError(exc.reason) from None
            log.warning("cancel %s: no clear answer: %r", order.request.client_order_id, exc)
            raise OrderError("no answer from the broker: check the order before retrying") from None
        if not order.status.is_final:
            self._touch(order)
            self._emit(EventKind.CANCEL_SENT, order)

    def _working_order(self, client_order_id: str) -> _Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise OrderError(f"unknown order {client_order_id}")
        if order.status.is_final:
            raise OrderError(f"order {client_order_id} is already {order.status.value.lower()}")
        return order

    # --- broker updates ---------------------------------------------------------------------

    async def run(self) -> None:
        """Apply the broker's updates as they arrive. Runs until cancelled."""
        async for update in self._broker.updates():
            self.apply(update)

    def apply(self, update: OrderUpdate) -> bool:
        """Apply one broker update. Returns True if it changed the order."""
        order = self._orders.get(update.client_order_id)
        if order is None:
            order = self._by_broker_id.get(update.broker_order_id)
        if order is None:
            self._park(update)
            return False
        if order.broker_order_id is None and update.broker_order_id:
            self._register_broker_id(order, update.broker_order_id)
            self._replay_parked(update.broker_order_id)  # older updates first
            if order.cancel_requested:
                self._spawn(self._send_cancel_if_requested(order))
        return self._apply_to(order, update)

    def _park(self, update: OrderUpdate) -> None:
        """Keep an update whose order we can't match yet (its place call hasn't returned)."""
        if update.broker_order_id not in self._parked and len(self._parked) >= MAX_PARKED:
            oldest = next(iter(self._parked))
            log.warning("dropping unmatched updates for broker order %s", oldest)
            del self._parked[oldest]
        self._parked.setdefault(update.broker_order_id, []).append(update)

    def _apply_to(self, order: _Order, update: OrderUpdate) -> bool:
        status = _normalise(update.status, update.filled_qty, order.request.qty)
        if update.filled_qty < order.filled_qty:
            return self._ignore(order, update, "stale: reports less filled than we know")
        if status is order.status:
            if update.filled_qty == order.filled_qty:
                return self._ignore(order, update, "duplicate")
        elif status not in ALLOWED[order.status]:
            return self._ignore(order, update, f"{order.status.value} can't become {status.value}")

        fill = self._fill_from(order, update)
        order.status = status
        order.filled_qty = update.filled_qty
        order.avg_price_paise = update.avg_price_paise
        if status in (S.CANCELLED, S.REJECTED):
            order.reason = update.reason
        if status.is_final:
            order.pending = Pending.NONE
        self._touch(order)
        self._emit(EventKind.UPDATED, order, detail=update.reason)
        if fill is not None:
            self._emit(EventKind.FILL, order, fill=fill)
        return True

    def _fill_from(self, order: _Order, update: OrderUpdate) -> Fill | None:
        """The new quantity traded since the last update we applied, and its price."""
        qty = update.filled_qty - order.filled_qty
        if qty <= 0:
            return None
        if update.last_fill_qty == qty and update.last_fill_price_paise > 0:
            return Fill(order.request.client_order_id, qty, update.last_fill_price_paise, True)
        # Work it out from the averages: total value now minus total value before.
        before = order.avg_price_paise * order.filled_qty
        after = update.avg_price_paise * update.filled_qty
        price = round((after - before) / qty)
        return Fill(order.request.client_order_id, qty, price, False)

    def _ignore(self, order: _Order, update: OrderUpdate, why: str) -> bool:
        log.debug("ignored update for %s: %s", order.request.client_order_id, why)
        self._emit(EventKind.IGNORED, order, detail=f"{update.status.value}: {why}")
        return False

    # --- reconciliation ---------------------------------------------------------------------

    def reconcile(self, broker_orders: Iterable[OrderUpdate]) -> list[OrderUpdate]:
        """Bring our orders in line with the broker's own list (after a reconnect or restart).

        The broker's list is the truth: each entry is applied like a normal update, which
        also settles UNCONFIRMED orders. An UNCONFIRMED order missing from the list never
        reached the broker, so it becomes REJECTED. Call this a few seconds after a failed
        place, not instantly, so a slow broker has time to list the order.

        Orders we didn't place are returned, and reported once (and again when their status
        changes) as EXTERNAL events, so the caller can show them.
        """
        external = []
        for snapshot in broker_orders:
            known = snapshot.client_order_id in self._orders or (
                snapshot.broker_order_id in self._by_broker_id
            )
            if known:
                self.apply(snapshot)
                continue
            external.append(snapshot)
            self._parked.pop(snapshot.broker_order_id, None)  # not ours: stop waiting for it
            if self._external.get(snapshot.broker_order_id) is not snapshot.status:
                self._external[snapshot.broker_order_id] = snapshot.status
                self._emit_external(snapshot)
        for order in self._orders.values():
            if order.pending is Pending.UNCONFIRMED and order.broker_order_id is None:
                order.pending = Pending.NONE
                self._reject_locally(order, "the broker never received this order")
        return external

    # --- helpers ----------------------------------------------------------------------------

    def _mark_unconfirmed(self, order: _Order, detail: str) -> None:
        order.pending = Pending.UNCONFIRMED
        self._touch(order)
        self._emit(EventKind.UNCONFIRMED, order, detail=detail)

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def settle(self) -> None:
        """Wait for background work (deferred cancels) to finish. For tests and shutdown."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _register_broker_id(self, order: _Order, broker_order_id: str) -> None:
        order.broker_order_id = broker_order_id
        self._by_broker_id[broker_order_id] = order
        if order.pending is Pending.UNCONFIRMED:
            order.pending = Pending.NONE

    def _replay_parked(self, broker_order_id: str) -> None:
        for update in self._parked.pop(broker_order_id, []):
            self.apply(update)

    def _reject_locally(self, order: _Order, reason: str) -> None:
        order.status = OrderStatus.REJECTED
        order.reason = reason
        self._touch(order)
        self._emit(EventKind.REJECTED, order, detail=reason)

    def _finish_locally(self, order: _Order) -> None:
        order.status = OrderStatus.CANCELLED
        order.reason = "cancelled before it was sent"
        self._touch(order)
        self._emit(EventKind.CANCELLED_LOCALLY, order, detail=order.reason)

    def _touch(self, order: _Order) -> None:
        order.updated_at = self._clock()

    def _emit(self, kind: EventKind, order: _Order, *, detail: str = "", fill: Fill | None = None):
        self._publish(
            OrderEvent(
                kind=kind,
                ts=self._clock(),
                account_id=self.account_id,
                client_order_id=order.request.client_order_id,
                order=order.view(),
                detail=detail,
                fill=fill,
            )
        )

    def _emit_external(self, snapshot: OrderUpdate) -> None:
        self._publish(
            OrderEvent(
                kind=EventKind.EXTERNAL,
                ts=self._clock(),
                account_id=self.account_id,
                client_order_id=snapshot.client_order_id,
                order=None,
                detail=f"broker order {snapshot.broker_order_id}: {snapshot.status.value}",
            )
        )

    def _publish(self, event: OrderEvent) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # a broken screen must never stop order handling
                log.exception("order event listener failed")


def _normalise(status: OrderStatus, filled_qty: int, qty: int) -> OrderStatus:
    """Brokers like Kite report a part-filled order as OPEN; we call it PARTIAL."""
    if status is OrderStatus.OPEN and 0 < filled_qty < qty:
        return OrderStatus.PARTIAL
    return status


def _describe_changes(qty: int | None, price: int | None, trigger: int | None) -> str:
    parts = []
    if qty is not None:
        parts.append(f"qty={qty}")
    if price is not None:
        parts.append(f"price_paise={price}")
    if trigger is not None:
        parts.append(f"trigger_paise={trigger}")
    return ", ".join(parts)
