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
  rejected: it stays PENDING, marked UNCONFIRMED (its risk still counted), until the
  broker's order list settles it (`reconcile`). The manager asks the broker again by
  itself, a few times over about three minutes (`follow_unconfirmed`). One list that
  doesn't show the order proves nothing (a slow broker lists it late): it becomes
  REJECTED only when UNSEEN_CHECKS lists over at least UNSEEN_AFTER all miss it, and
  the reason says so. Still unsettled after that: it stays UNCONFIRMED until a person
  checks the broker's own order book and settles it (`settle_unconfirmed`). Never a
  blind retry (#259).
- Cancel before the broker's id is known is remembered and sent as soon as it is.

One manager per broker account (the "sharding" in the architecture): accounts never
share state, so they can later run in separate processes.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.order_spec import OrderSpec, spec_problem
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderUpdate
from mocktrade.metrics import METRICS, since_us
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
UNSEEN_CHECKS = 3  # broker order lists that must all miss an UNCONFIRMED order...
UNSEEN_AFTER = timedelta(seconds=60)  # ...the last at least this long after it was sent
FOLLOW_UP_S = (2, 5, 10, 20, 30, 60, 60)  # when to ask the broker again (about 3 minutes)
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
    unseen: list[datetime] = field(default_factory=list)  # broker lists that didn't show it
    following: bool = False  # a follow-up is asking the broker about it
    cancel_unclear: bool = False  # the last cancel got no clear answer: it may have worked
    change_sent_at: datetime | None = None  # a change with no clear answer was sent then
    terms_at: datetime | None = None  # when our terms were last confirmed (a change, the broker)

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
        spec: Callable[[Instrument], OrderSpec] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        follow_up_s: Sequence[float] = FOLLOW_UP_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.account_id = account_id
        self._follow_up_s = tuple(follow_up_s)
        self._sleep = sleep
        self._followers: set[asyncio.Task] = set()  # not awaited by settle(): they wait
        self._spec = spec  # what the broker offers per instrument (order types, validities...)
        self._broker = broker
        self._broker_name = getattr(broker, "name", type(broker).__name__)
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
        started = time.perf_counter_ns()
        try:
            broker_order_id = await self._broker.place(request)
            self._timed("place", started)
        except BrokerError as exc:
            self._reject_locally(order, exc.reason)
            return order.view()
        except asyncio.CancelledError:
            self._mark_unconfirmed(order, "stopped while waiting for the broker")
            raise
        except Exception as exc:  # no clear answer: never guess, reconcile will tell
            log.warning("place %s: no clear answer: %r", request.client_order_id, exc)
            self._mark_unconfirmed(order, f"no answer from broker: {exc!r}")
            self.follow_unconfirmed()  # ask the broker again, by itself
            return order.view()

        self._register_broker_id(order, broker_order_id)
        self._touch(order)
        self._emit(EventKind.SENT, order)
        self._replay_parked(broker_order_id)
        await self._send_cancel_if_requested(order)
        return order.view()

    def adopt(self, request: OrderRequest, broker_order_id: str, detail: str) -> OrderView:
        """Take on an order the BROKER placed for us (a GTT that fired): from now on it is
        ours like any other (its fills reach positions and the audit trail), under a client
        id of ours. Updates that came before it was adopted are replayed. Adopting twice
        returns the same order."""
        known = self._by_broker_id.get(broker_order_id)
        if known is not None:
            return known.view()
        request = replace(request, client_order_id=self._ids.next())
        now = self._clock()
        order = _Order(request=request, created_at=now, updated_at=now, sent=True)
        self._orders[request.client_order_id] = order
        self._emit(EventKind.SUBMITTED, order, detail=detail)
        self._register_broker_id(order, broker_order_id)
        self._emit(EventKind.SENT, order, detail=detail)
        self._replay_parked(broker_order_id)
        return order.view()

    def _timed(self, op: str, started_ns: int) -> None:
        """How long the broker took to accept a request (our side of the round trip)."""
        METRICS.observe(
            f"broker_ms{{op={op},broker={self._broker_name}}}", since_us(started_ns) / 1000, "ms"
        )

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
        if self._spec is not None:
            return spec_problem(
                self._spec(instrument), request.order_type, request.validity, request.product
            )
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
        """Change a working order. Raises OrderError with a readable reason if it can't.

        One change at a time per order (#260): a second one while the first is on its way is
        refused, so answers can never arrive out of order and undo a newer change. A change
        with no clear answer may or may not have been made: until the broker's own terms say
        (an update, or its order list, asked by a follow-up), the order counts the larger
        quantity of the two, so risk is never understated."""
        order = self._working_order(client_order_id)
        if order.broker_order_id is None:
            raise OrderError("the broker hasn't acknowledged this order yet: try again shortly")
        if order.pending is Pending.CANCEL:
            raise OrderError("a cancel is already on its way for this order")
        if order.pending is Pending.MODIFY:
            raise OrderError("a change to this order is already on its way: wait for it")
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
        order.pending = Pending.MODIFY  # from here: no second change, even while gates wait
        try:
            if (reason := await self._gate.admit_modify(order.view(), new)) is not None:
                raise OrderError(reason)
            if order.status.is_final or order.cancel_requested:  # while it waited at the gate
                raise OrderError("the order finished or is being cancelled: nothing changed")
        except OrderError:
            if order.pending is Pending.MODIFY:
                order.pending = Pending.NONE
            raise
        started = time.perf_counter_ns()
        try:
            await self._broker.modify(
                order.broker_order_id, qty=qty, price_paise=price_paise, trigger_paise=trigger_paise
            )
            self._timed("modify", started)
        except BrokerError as exc:
            if order.pending is Pending.MODIFY:
                order.pending = Pending.NONE
            raise OrderError(exc.reason) from None
        except Exception as exc:  # it may or may not have been made: never guess
            log.warning("modify %s: no clear answer: %r", client_order_id, exc)
            if not order.status.is_final and order.pending is Pending.MODIFY:
                old = order.request
                order.request = replace(new, qty=max(old.qty, new.qty))  # the worse case
                order.change_sent_at = self._clock()
                self._touch(order)
                self._emit(
                    EventKind.MODIFY_UNCONFIRMED, order,
                    detail=f"{_describe_changes(qty, price_paise, trigger_paise)}: no answer "
                    f"({exc!r}); counting {order.request.qty} until the broker's terms say",
                )  # fmt: skip
                self.follow_unconfirmed()
            raise OrderError(
                "no answer from the broker: the change may or may not have been made. "
                "MockTrade is asking the broker; risk counts the larger quantity meanwhile"
            ) from None
        if order.pending is Pending.MODIFY:
            order.pending = Pending.NONE
        if order.status.is_final:  # finished while the modify was on its way
            return order.view()
        order.request = new
        order.terms_at = self._clock()  # broker terms older than this can't undo the change
        self._touch(order)
        self._emit(
            EventKind.MODIFY_SENT, order, detail=_describe_changes(qty, price_paise, trigger_paise)
        )
        return order.view()

    async def cancel(self, client_order_id: str) -> OrderView:
        """Ask to cancel a working order. The CANCELLED status arrives as a broker update."""
        order = self._working_order(client_order_id)
        if order.cancel_requested and not order.cancel_unclear:
            return order.view()  # already asked: a second click changes nothing
        if order.cancel_unclear:  # the last one got no answer: asking again is safe
            order.cancel_unclear = False
            order.cancel_requested = order.cancel_sent = False
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
        started = time.perf_counter_ns()
        try:
            await self._broker.cancel(order.broker_order_id)
            self._timed("cancel", started)
        except BrokerError as exc:  # a clear refusal (e.g. already filled): nothing changed
            if not order.status.is_final:
                order.pending = Pending.NONE
            order.cancel_requested = order.cancel_sent = False
            self._touch(order)
            raise OrderError(exc.reason) from None
        except Exception as exc:  # it may have worked: still "cancel on its way" (#260)
            log.warning("cancel %s: no clear answer: %r", order.request.client_order_id, exc)
            order.cancel_unclear = True
            order.change_sent_at = self._clock()
            self._touch(order)
            self._emit(EventKind.CANCEL_SENT, order, detail=f"no clear answer ({exc!r})")
            self.follow_unconfirmed()
            raise OrderError(
                "no answer from the broker: the cancel may have gone through. MockTrade is "
                "asking the broker; you can press cancel again"
            ) from None
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

    # --- restart ------------------------------------------------------------------------------

    def restore(self, views: Iterable[OrderView]) -> int:
        """Load orders saved before a restart (from the event log). Call before `run` and
        then `reconcile` with the broker's list. Returns how many were loaded.

        An order that was PENDING without a broker id may or may not have reached the
        broker when we stopped, so it comes back UNCONFIRMED: reconcile decides. A change or
        a cancel that was on its way comes back in doubt the same way.
        """
        loaded = 0
        for view in views:
            if view.client_order_id in self._orders or view.request.account_id != self.account_id:
                continue
            pending = view.pending
            if view.status is S.PENDING and view.broker_order_id is None:
                pending = Pending.UNCONFIRMED
            order = _Order(
                request=view.request,
                created_at=view.created_at,
                updated_at=view.updated_at,
                broker_order_id=view.broker_order_id,
                status=view.status,
                filled_qty=view.filled_qty,
                avg_price_paise=view.avg_price_paise,
                reason=view.reason,
                pending=pending,
                sent=True,
                cancel_requested=pending is Pending.CANCEL,
                cancel_sent=pending is Pending.CANCEL,
                # a change or cancel on its way when we stopped: whether it happened is not
                # known, so both are in doubt until the broker's list says (#260)
                cancel_unclear=pending is Pending.CANCEL,
                change_sent_at=view.updated_at if pending is Pending.MODIFY else None,
            )
            self._orders[view.client_order_id] = order
            if view.broker_order_id:
                self._by_broker_id[view.broker_order_id] = order
            loaded += 1
        return loaded

    # --- broker updates ---------------------------------------------------------------------

    async def run(self) -> None:
        """Apply the broker's updates as they arrive. Runs until cancelled."""
        async for update in self._broker.updates():
            self.apply(update)

    def apply(self, update: OrderUpdate, *, current: bool = False) -> bool:
        """Apply one broker update. Returns True if it changed the order.

        `current`: from the broker's order list, fetched now (reconcile), so its terms are
        the order as it is. A streamed update's terms count only when it is newer than our
        last confirmed change: an old one arriving late can't undo a newer change (#260)."""
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
        return self._apply_to(order, update, current)

    def _park(self, update: OrderUpdate) -> None:
        """Keep an update whose order we can't match yet (its place call hasn't returned)."""
        if update.broker_order_id not in self._parked and len(self._parked) >= MAX_PARKED:
            oldest = next(iter(self._parked))
            log.warning("dropping unmatched updates for broker order %s", oldest)
            del self._parked[oldest]
        self._parked.setdefault(update.broker_order_id, []).append(update)

    def _apply_to(self, order: _Order, update: OrderUpdate, current: bool = False) -> bool:
        status = _normalise(update.status, update.filled_qty, order.request.qty)
        if update.filled_qty < order.filled_qty:
            return self._ignore(order, update, "stale: reports less filled than we know")
        terms = self._broker_terms(order, update, current)
        if status is order.status:
            if update.filled_qty == order.filled_qty and terms is None:
                return self._ignore(order, update, "duplicate")
        elif status not in ALLOWED[order.status]:
            return self._ignore(order, update, f"{order.status.value} can't become {status.value}")

        fill = self._fill_from(order, update)
        order.status = status
        order.filled_qty = update.filled_qty
        order.avg_price_paise = update.avg_price_paise
        if status in (S.CANCELLED, S.REJECTED):
            order.reason = update.reason
        detail = update.reason
        if terms is not None:  # the broker's word on what the order is now
            if terms != order.request:
                detail = detail or f"the broker's terms: {_terms_text(terms)}"
            order.request = terms
            order.terms_at = self._clock() if current else update.ts
            if order.pending is Pending.MODIFY and order.change_sent_at is not None:
                order.pending = Pending.NONE  # a change with no answer: settled by its terms
                order.change_sent_at = None
        if status.is_final:
            order.pending = Pending.NONE
            order.cancel_unclear = False
            order.change_sent_at = None
        self._touch(order)
        self._emit(EventKind.UPDATED, order, detail=detail)
        if fill is not None:
            self._emit(EventKind.FILL, order, fill=fill)
        return True

    @staticmethod
    def _broker_terms(order: _Order, update: OrderUpdate, current: bool) -> OrderRequest | None:
        """The order's request with the broker's terms, when the update gives them and they
        count; None when it gives none, or they are older than ours, or change nothing."""
        if update.qty is None and update.price_paise is None and update.trigger_paise is None:
            return None
        if not current:
            since = order.change_sent_at or order.terms_at
            if since is not None and update.ts < since:
                return None  # older than our last change: it can't undo it
        r = order.request
        terms = replace(
            r,
            qty=update.qty if update.qty is not None else r.qty,
            price_paise=update.price_paise if update.price_paise is not None else r.price_paise,
            trigger_paise=(
                update.trigger_paise if update.trigger_paise is not None else r.trigger_paise
            ),
        )
        doubt = order.pending is Pending.MODIFY and order.change_sent_at is not None
        return terms if terms != r or doubt else None

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
        said = f" ({update.reason})" if update.reason else ""  # e.g. a status we don't know
        self._emit(EventKind.IGNORED, order, detail=f"{update.status.value}: {why}{said}")
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
                self.apply(snapshot, current=True)
                self._cancel_did_not_take(snapshot)
                continue
            external.append(snapshot)
            self._parked.pop(snapshot.broker_order_id, None)  # not ours: stop waiting for it
            if self._external.get(snapshot.broker_order_id) is not snapshot.status:
                self._external[snapshot.broker_order_id] = snapshot.status
                self._emit_external(snapshot)
        now = self._clock()
        for order in self._orders.values():
            if not self._unsettled(order):  # (listed: its broker id settled it above)
                continue
            order.unseen.append(now)  # one more list without it: evidence, not proof
            if len(order.unseen) >= UNSEEN_CHECKS and now - order.created_at >= UNSEEN_AFTER:
                first, last = order.unseen[0], order.unseen[-1]
                order.pending = Pending.NONE
                self._reject_locally(
                    order,
                    f"the broker never received this order: not in its order list at "
                    f"{len(order.unseen)} checks from {first:%H:%M:%S} to {last:%H:%M:%S} UTC, "
                    f"{(now - order.created_at).total_seconds():.0f} s after it was sent",
                )
        return external

    def follow_unconfirmed(self) -> None:
        """Ask the broker again about every UNCONFIRMED order, in the background, a few times
        over about three minutes (after a failed place, and after a restart's recovery)."""
        for order in self._orders.values():
            if self._in_doubt(order) and not order.following:
                order.following = True
                task = asyncio.get_running_loop().create_task(self._follow(order))
                self._followers.add(task)
                task.add_done_callback(self._followers.discard)

    async def _follow(self, order: _Order) -> None:
        try:
            for delay in self._follow_up_s:
                if not self._in_doubt(order):
                    return  # settled (by an update, a reconcile or a person): stop asking
                await self._sleep(delay)
                if not self._in_doubt(order):
                    return
                try:
                    self.reconcile(await self._broker.orders())
                except Exception as exc:  # the broker can't answer either: try again later
                    log.warning("follow-up of %s: %r", order.request.client_order_id, exc)
            if self._in_doubt(order):
                what = ("the order" if self._unsettled(order)
                        else "the cancel" if order.cancel_unclear else "the change")  # fmt: skip
                self._emit(
                    EventKind.UNCONFIRMED, order,
                    detail=f"{what} is still not settled after asking the broker "
                    f"{len(self._follow_up_s)} times: check the broker's own order book"
                    + (", then settle it here" if self._unsettled(order) else ""),
                )  # fmt: skip
        finally:
            order.following = False

    def settle_unconfirmed(self, client_order_id: str, *, by: str) -> OrderView:
        """A person checked the broker's own order book: the order isn't there. Only for an
        UNCONFIRMED order with no broker id (anything the broker named settles itself)."""
        order = self._orders.get(client_order_id)
        if order is None:
            raise OrderError(f"no order {client_order_id}")
        if not self._unsettled(order):
            raise OrderError("only an unconfirmed order the broker never named can be settled")
        order.pending = Pending.NONE
        self._reject_locally(
            order, f"settled by {by}: not in the broker's own order book, so never placed"
        )
        return order.view()

    async def aclose(self) -> None:
        """Stop the follow-ups (shutdown, tests)."""
        for task in list(self._followers):
            task.cancel()
        await asyncio.gather(*list(self._followers), return_exceptions=True)

    def _cancel_did_not_take(self, snapshot: OrderUpdate) -> None:
        """An unanswered cancel, and the broker's current list still has the order working:
        the cancel didn't happen. Say so, and let the trader cancel again."""
        order = self._orders.get(snapshot.client_order_id) or self._by_broker_id.get(
            snapshot.broker_order_id
        )
        if order is None or not order.cancel_unclear or order.status.is_final:
            return
        order.cancel_unclear = order.cancel_requested = order.cancel_sent = False
        order.change_sent_at = None
        order.pending = Pending.NONE
        self._touch(order)
        why = "the cancel didn't go through: the broker still has it working"
        self._emit(EventKind.UPDATED, order, detail=why)

    @classmethod
    def _in_doubt(cls, order: _Order) -> bool:
        """Something sent about it got no clear answer: the order, a change, or a cancel."""
        return (
            cls._unsettled(order)
            or (order.pending is Pending.MODIFY and order.change_sent_at is not None)
            or order.cancel_unclear
        )

    @staticmethod
    def _unsettled(order: _Order) -> bool:
        """No answer yet (an order the broker named is never UNCONFIRMED: its id settles it)."""
        return order.pending is Pending.UNCONFIRMED

    async def resync(self) -> list[OrderUpdate]:
        """Fetch the broker's order book and reconcile with it (after a reconnect, at
        startup, or a few seconds after an UNCONFIRMED order). Returns external orders."""
        return self.reconcile(await self._broker.orders())

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


def _terms_text(r: OrderRequest) -> str:
    parts = [f"qty {r.qty}"]
    if r.price_paise is not None:
        parts.append(f"price {r.price_paise}")
    if r.trigger_paise is not None:
        parts.append(f"trigger {r.trigger_paise}")
    return ", ".join(parts)


def _describe_changes(qty: int | None, price: int | None, trigger: int | None) -> str:
    parts = []
    if qty is not None:
        parts.append(f"qty={qty}")
    if price is not None:
        parts.append(f"price_paise={price}")
    if trigger is not None:
        parts.append(f"trigger_paise={trigger}")
    return ", ".join(parts)
