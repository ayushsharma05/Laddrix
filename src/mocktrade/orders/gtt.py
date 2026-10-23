"""GTTs for one account: checked, placed at the broker, watched, and adopted when they fire.

    place(request) ─► the broker's GTT spec (kinds, tick, lots, distance from the last price)
                   ─► each leg through the risk checks *as if at its trigger price*
                   ─► broker.place_gtt ─► audited (GTT_PLACED), told to the screens
    every few seconds (brokers don't push GTT changes): broker.gtts()
                   ─► a changed status is audited (GTT_TRIGGERED, GTT_DELETED, ...) and told
                   ─► a GTT that fired placed an order at the broker: the order manager
                      adopts it, so its fills reach positions and the audit trail like
                      any order of ours

What a GTT can't do: the risk checks run when it is placed, not when it fires (the broker
places that order without asking us). Its legs are small, closing orders by design, and
the position limits and kill switches still apply to everything else.

While a kill switch is on (STOP, #276), each refresh also sweeps the GTTs:

    every leg only reduces the position in its product ─► kept (a stop-loss still protects)
    any leg could open or add                          ─► deleted at the broker (GTT_STOPPED);
                                                          an unclear delete is said
                                                          (GTT_STOP_UNCLEAR) and tried again
                                                          on the next refresh
    one fired anyway, and its order would add risk     ─► that order is cancelled at once
                                                          (GTT_FIRED_IN_STOP)

A position closed after STOP (Flatten) turns its protective GTTs into opening ones: the next
sweep deletes them.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from mocktrade.broker.base import BrokerError, GttBook
from mocktrade.domain.gtt import (
    GttKind,
    GttLeg,
    GttRequest,
    GttSpec,
    GttStatus,
    GttView,
    gtt_problems,
)
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.orders.manager import OrderManager
from mocktrade.risk.checks import RiskGate

log = logging.getLogger(__name__)

POLL_S = 5.0


class GttError(ValueError):
    """A GTT that can't be placed, changed or deleted: the message says why, in words."""


class GttService:
    def __init__(
        self,
        account_id: str,
        broker: GttBook,
        orders: OrderManager,
        risk: RiskGate,
        instruments: Callable[[str], Instrument | None],
        prices: Callable[[str], Tick | None],
        *,
        audit: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        poll_s: float = POLL_S,
        stopped: Callable[[], str | None] = lambda: None,  # why trading is stopped, if it is
        position: Callable[[str, Product], int] = lambda _i, _p: 0,  # qty in one product
    ) -> None:
        self._stopped = stopped
        self._position = position
        self.account_id = account_id
        self._broker = broker
        self._orders = orders
        self._risk = risk
        self._instrument = instruments
        self._price = prices
        self._audit = audit
        self._clock = clock
        self._poll_s = poll_s
        self._gtts: dict[str, GttView] = {}
        self._listeners: list[Callable[[GttView], None]] = []

    # --- reading ------------------------------------------------------------------------------

    def spec(self, instrument_id: str) -> GttSpec:
        instrument = self._instrument(instrument_id)
        if instrument is None:
            raise GttError(f"unknown instrument {instrument_id}")
        return self._broker.gtt_spec(instrument)

    def list(self) -> list[GttView]:
        return sorted(
            self._gtts.values(), key=lambda v: v.created_at or datetime.min.replace(tzinfo=UTC)
        )

    def get(self, gtt_id: str) -> GttView | None:
        return self._gtts.get(gtt_id)

    def subscribe(self, listener: Callable[[GttView], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- the trader's controls ------------------------------------------------------------------

    async def place(self, request: GttRequest) -> GttView:
        ltp = await self._check(request)
        try:
            gtt_id = await self._broker.place_gtt(request, ltp)
        except BrokerError as exc:
            raise GttError(f"the broker refused the GTT: {exc.reason}") from None
        except Exception as exc:  # no clear answer: the GTT may exist; the next poll will say
            log.warning("place GTT: no clear answer: %r", exc)
            raise GttError(
                "no clear answer from the broker: check the GTT list before placing it again"
            ) from None
        now = self._clock()
        view = GttView(gtt_id, request, GttStatus.ACTIVE, created_at=now, updated_at=now)
        self._changed(view, "GTT_PLACED", f"GTT {gtt_id} placed: {_words(request)}")
        return view

    async def modify(self, gtt_id: str, request: GttRequest) -> GttView:
        old = self._active(gtt_id)
        ltp = await self._check(request)
        try:
            await self._broker.modify_gtt(gtt_id, request, ltp)
        except BrokerError as exc:
            raise GttError(f"the broker refused the change: {exc.reason}") from None
        except Exception as exc:
            log.warning("modify GTT %s: no clear answer: %r", gtt_id, exc)
            raise GttError("no clear answer from the broker: check the GTT list") from None
        view = replace(old, request=request, updated_at=self._clock())
        self._changed(view, "GTT_MODIFIED", f"GTT {gtt_id} changed: {_words(request)}")
        return view

    async def delete(self, gtt_id: str, user_id: str) -> GttView:
        old = self._active(gtt_id)
        try:
            await self._broker.delete_gtt(gtt_id)
        except BrokerError as exc:
            raise GttError(f"the broker refused to delete it: {exc.reason}") from None
        except Exception as exc:
            log.warning("delete GTT %s: no clear answer: %r", gtt_id, exc)
            raise GttError("no clear answer from the broker: check the GTT list") from None
        view = replace(old, status=GttStatus.DELETED, updated_at=self._clock())
        self._changed(view, "GTT_DELETED", f"GTT {gtt_id} deleted by {user_id}")
        return view

    # --- keeping up with the broker ---------------------------------------------------------------

    async def refresh(self) -> None:
        """Read the broker's GTTs: report what changed, adopt the orders of those that fired."""
        for theirs in await self._broker.gtts():
            ours = self._gtts.get(theirs.gtt_id)
            if ours is not None:  # keep who placed it; the broker doesn't know our users
                theirs = replace(
                    theirs,
                    request=replace(theirs.request, user_id=ours.request.user_id),
                    created_at=theirs.created_at or ours.created_at,
                )
            changed = ours is None or (theirs.status, theirs.order_id) != (
                ours.status,
                ours.order_id,
            )
            if not changed:
                continue
            theirs = replace(theirs, updated_at=self._clock())
            if theirs.status is GttStatus.TRIGGERED and theirs.order_id:
                adopted = self._adopt(theirs)
                await self._fired_in_stop(theirs, adopted)
            if ours is None:
                self._changed(theirs, "GTT_FOUND", f"GTT {theirs.gtt_id} at the broker: "
                              f"{theirs.status.value}, {_words(theirs.request)}")  # fmt: skip
            elif theirs.status is not ours.status or theirs.order_id != ours.order_id:
                kind = f"GTT_{theirs.status.name}"
                detail = f"GTT {theirs.gtt_id} {theirs.status.value}"
                if theirs.fired_leg is not None and theirs.order_id:
                    detail += f": leg {theirs.fired_leg + 1} placed order {theirs.order_id}"
                if theirs.reason:
                    detail += f" ({theirs.reason})"
                self._changed(theirs, kind, detail)
        await self.sweep()

    # --- STOP (#276) ------------------------------------------------------------------------------

    def protective(self, view: GttView) -> bool:
        """Every leg only reduces the position it is for (in its product), now."""
        held = self._position(view.request.instrument_id, view.request.product)
        return all(_reduces(leg, held) for leg in view.request.legs)

    async def sweep(self) -> "list[str]":  # (`list` here is the method above)
        """While trading is stopped: delete each active GTT that could open or add a position
        (see the module notes). Returns what it did, in words."""
        why = self._stopped()
        if why is None:
            return []
        done = []
        for view in list(self._gtts.values()):
            if view.status is not GttStatus.ACTIVE or self.protective(view):
                continue
            try:
                await self._broker.delete_gtt(view.gtt_id)
            except Exception as exc:  # refused or no answer: it may still be live; say so
                reason = exc.reason if isinstance(exc, BrokerError) else type(exc).__name__
                words = (f"GTT {view.gtt_id} could open a position while trading is stopped, "
                         f"and deleting it failed ({reason}): trying again")  # fmt: skip
                self._changed(replace(view, reason=words), "GTT_STOP_UNCLEAR", words)
                done.append(words)
                continue
            words = (f"GTT {view.gtt_id} deleted: trading is stopped ({why}) and it could open "
                     "a position")  # fmt: skip
            self._changed(replace(view, status=GttStatus.DELETED, updated_at=self._clock(),
                                  reason=words), "GTT_STOPPED", words)  # fmt: skip
            done.append(words)
        return done

    async def _fired_in_stop(self, view: GttView, adopted) -> None:
        """A GTT fired while trading is stopped: its order stays only if it reduces."""
        why = self._stopped()
        if why is None or view.fired_leg is None:
            return
        leg = view.request.legs[view.fired_leg]
        if _reduces(leg, self._position(view.request.instrument_id, view.request.product)):
            return  # a stop-loss or target doing its job
        words = (f"GTT {view.gtt_id} fired while trading is stopped ({why}): its order "
                 f"{adopted.client_order_id} would add risk, so it is being cancelled")  # fmt: skip
        try:
            await self._orders.cancel(adopted.client_order_id)
        except Exception as exc:  # e.g. already filled: say so, a person must look
            words = (f"GTT {view.gtt_id} fired while trading is stopped ({why}); cancelling "
                     f"its order {adopted.client_order_id} failed: {exc}")  # fmt: skip
        if self._audit is not None:
            self._audit("GTT_FIRED_IN_STOP", words, {"gtt_id": view.gtt_id,
                        "client_order_id": adopted.client_order_id})  # fmt: skip

    async def run(self) -> None:
        """Poll the broker's GTTs until cancelled. A broker that can't be asked yet (Kite
        before the daily login) is simply asked again later."""
        while True:
            try:
                await self.refresh()
            except BrokerError as exc:
                log.debug("GTT refresh for %s: %s", self.account_id, exc.reason)
            except Exception:
                log.exception("GTT refresh for %s failed", self.account_id)
            await asyncio.sleep(self._poll_s)

    # --- inside -------------------------------------------------------------------------------

    async def _check(self, request: GttRequest) -> int:
        """The broker's rules, then each leg through the risk checks at its trigger price.
        Returns the last price the GTT is placed against."""
        if request.account_id != self.account_id:
            raise GttError(f"this GTT is for {request.account_id}, not {self.account_id}")
        spec = self.spec(request.instrument_id)
        tick = self._price(request.instrument_id)
        if tick is None:
            raise GttError(f"no price yet for {request.instrument_id}: a GTT needs the last price")
        problems = gtt_problems(spec, request, tick.ltp_paise)
        active = sum(1 for v in self._gtts.values() if v.status is GttStatus.ACTIVE)
        if active >= spec.max_active:
            problems.append(
                f"{active} GTTs are already active (the broker allows {spec.max_active})"
            )
        if not problems:
            for n, leg in enumerate(request.legs, 1):
                order = OrderRequest(
                    f"GTTleg{n}", request.account_id, request.user_id, request.instrument_id,
                    leg.side, leg.qty, OrderType.LIMIT, request.product,
                    price_paise=leg.price_paise,
                )  # fmt: skip
                reason = await self._risk.admit_at(order, leg.trigger_paise)
                if reason is not None:
                    problems.append(reason if len(request.legs) == 1 else f"leg {n}: {reason}")
        if problems:
            raise GttError("; ".join(problems))
        return tick.ltp_paise

    def _active(self, gtt_id: str) -> GttView:
        view = self._gtts.get(gtt_id)
        if view is None:
            raise GttError(f"no GTT {gtt_id}")
        if view.status is not GttStatus.ACTIVE:
            raise GttError(f"GTT {gtt_id} is {view.status.value}: it can't be changed")
        return view

    def _adopt(self, view: GttView):
        assert view.order_id is not None and view.fired_leg is not None
        leg, r = view.request.legs[view.fired_leg], view.request
        order = OrderRequest(
            "", self.account_id, r.user_id or "gtt", r.instrument_id, leg.side, leg.qty,
            OrderType.LIMIT, r.product, price_paise=leg.price_paise,
        )  # fmt: skip
        return self._orders.adopt(order, view.order_id,
                                  f"placed by the broker for GTT {view.gtt_id}")  # fmt: skip

    def _changed(self, view: GttView, kind: str, detail: str) -> None:
        self._gtts[view.gtt_id] = view
        if self._audit is not None:
            self._audit(kind, detail, {"gtt_id": view.gtt_id, "user_id": view.request.user_id,
                                       "instrument_id": view.request.instrument_id})  # fmt: skip
        for listener in list(self._listeners):
            try:
                listener(view)
            except Exception:
                log.exception("GTT listener failed")


def _reduces(leg: GttLeg, held: int) -> bool:
    """This leg only takes away from a position of `held` (+ long, - short)."""
    return held != 0 and leg.side.sign == -(1 if held > 0 else -1) and leg.qty <= abs(held)


def _words(r: GttRequest) -> str:
    legs = " / ".join(
        f"{leg.side.value} {leg.qty} at {leg.price_paise / 100:,.2f} "
        f"when {leg.trigger_paise / 100:,.2f}"
        for leg in r.legs
    )
    return f"{r.kind.value} {r.instrument_id} {r.product.value}: {legs}"


def gtt_request_from(body: dict[str, Any], account_id: str, user_id: str) -> GttRequest:
    """A GTT from the API's JSON (screens send what the spec told them)."""
    try:
        legs = tuple(
            GttLeg(int(leg["trigger_paise"]), Side(leg["side"]), int(leg["qty"]),
                   int(leg["price_paise"]))
            for leg in body["legs"]
        )  # fmt: skip
        return GttRequest(account_id, user_id, str(body["instrument_id"]),
                          Product(body["product"]), GttKind(body["kind"]), legs)  # fmt: skip
    except (KeyError, ValueError, TypeError) as exc:
        raise GttError(f"not a GTT: {exc}") from None
