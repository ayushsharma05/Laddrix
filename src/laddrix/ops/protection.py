"""On every screen (the alert band): positions that had protection and have lost it (V1.2, #328).

A stop held by Laddrix (trailing stop, OCO, bracket) can stop protecting while its position
stays open: the server restarted (those algos don't survive one), the trader paused or stopped
it, or it failed. Nothing at the exchange stands in for it, so the trader must know at once.

    at start, after the account's recovery   ─► every protection algo lost by the restart
    an algo event (PAUSED, STOPPED, FAILED…) ─► its position, if one is open
                                               │
    check() (every few seconds, and at once)  ─► alert "unprotected:<account>:<instrument>"
                                                  (ERROR, with the open quantity and why)
    cleared when the position is flat, or covered again: a protection algo working on it, a
    working stop order (SL, SL-M) on the closing side, or an active GTT

Only lost protection is flagged. A position that was never protected is the trader's choice,
and flagging every one would teach people to ignore the band. The server decides; the screens
only show the band (the same alert stream as every other warning).

What Laddrix guarantees (#264), and what it doesn't:
- A stop held by Laddrix works only while the server runs. During an outage nothing at the
  exchange stands in for it. A stop that must survive an outage has to be held by the broker:
  a stop-loss order (SL, SL-M) or a GTT. Those count as covering the position.
- A lost stop stays flagged until it is resolved: every flag and every resolution is a note
  in the audit trail (UNPROTECTED, PROTECTION_RESOLVED), and after a restart the unresolved
  ones come back. It is never reported once and then forgotten.
- A stop is never restarted by itself. A person restores it ("Restore stop"), which starts the
  same stop again for the position as it is now, and only when that can't duplicate an exit:
  the account's state is rebuilt, the broker agrees on the position's size, no order of the old
  stop is still working or unanswered, and no other stop (running or paused) covers it.
  A bracket comes back as an OCO exit at its levels (its entry is done); a trailing stop
  starts trailing again from the current price.
"""

import logging
from collections.abc import Iterable
from typing import Any

from laddrix.algos.engine import AlgoEvent, AlgoState, AlgoView
from laddrix.domain.orders import OrderType, Product, Side
from laddrix.ops.alerts import Alerts
from laddrix.orders.model import Pending

log = logging.getLogger(__name__)

PROTECTION = "protection"
LOST_KINDS = ("PAUSED", "STOPPED", "FAILED", "DONE")  # DONE with a position open: its exit missed
COVERED_KINDS = ("STARTED", "RESUMED")
STOPS = (OrderType.SL, OrderType.SL_M)
FLAGGED, RESOLVED = "UNPROTECTED", "PROTECTION_RESOLVED"  # audit notes: the durable record


GUARANTEE = (
    "A stop held by Laddrix (trailing stop, OCO, bracket) works only while the server runs; "
    "during an outage nothing at the exchange stands in for it.",
    "A stop that must survive an outage has to be held by the broker: a stop-loss order "
    "(SL, SL-M) or a GTT.",
    "After a restart a lost stop stays flagged until you restore it, cover the position "
    "another way, or close it. It is never restarted by itself.",
)


class RestoreError(Exception):
    pass


def key(account_id: str, instrument_id: str) -> str:
    return f"unprotected:{account_id}:{instrument_id}"


class ProtectionGuard:
    def __init__(self, platform, alerts: Alerts) -> None:
        self._platform = platform
        self._alerts = alerts
        self._flags: dict[tuple[str, str], str] = {}  # (account, instrument) -> why, in words
        self._lost: dict[tuple[str, str], dict[str, Any]] = {}  # the stop it had, to restore
        self._raised: set[tuple[str, str]] = set()
        self._watched: set[str] = set()
        self._watch()

    def _watch(self) -> None:
        """Every account's algos, including one an admin added since."""
        for account_id, runtime in self._platform.accounts.items():
            if account_id not in self._watched:
                self._watched.add(account_id)
                runtime.algos.subscribe(lambda e, a=account_id: self.on_algo(a, e))

    def flagged(self) -> dict[tuple[str, str], str]:
        return dict(self._flags)

    def after_restart(self, account_ids: Iterable[str] | None = None) -> None:
        """At start: the flags still unresolved before the restart (from the audit notes), and
        the protection algos that were running when the server stopped."""
        ids = list(self._platform.accounts if account_ids is None else account_ids)
        log_ = getattr(self._platform, "event_log", None)
        for account_id in ids:
            if log_ is None:
                break
            for e in log_.notes(account_id, [FLAGGED, RESOLVED]):
                where = (account_id, str(e.extra.get("instrument_id", "")))
                if e.kind == FLAGGED:
                    self._flags[where] = str(e.extra.get("why", e.detail))
                    if e.extra.get("algo"):
                        self._lost[where] = e.extra["algo"]
                else:
                    self._flags.pop(where, None)
                    self._lost.pop(where, None)
        for account_id in ids:
            for lost in self._platform.lost_protection(account_id):
                self._flag((account_id, lost.instrument_id),
                           f"its stop ({lost.words}) was running when the server stopped "
                           "and is not running now", getattr(lost, "algo", None))  # fmt: skip
        self.check()

    def on_algo(self, account_id: str, event: AlgoEvent) -> None:
        algo = event.algo
        if not self._is_protection(account_id, algo.code):
            return
        where = (account_id, algo.instrument_id)
        if event.kind in LOST_KINDS:
            why = f"its {algo.code} ({algo.algo_id}) {event.kind.lower()}"
            reason = event.detail or algo.reason
            self._flag(where, f"{why}: {reason}" if reason else why, _algo_record(algo))
        elif event.kind in COVERED_KINDS:
            self._clear(where, f"covered again: {algo.code} {algo.algo_id} {event.kind.lower()}")
        self.check()

    async def run_check(self) -> None:
        """The scheduler's job."""
        self.check()

    def check(self) -> None:
        self._watch()
        for where, why in list(self._flags.items()):
            account_id, instrument_id = where
            runtime = self._platform.accounts.get(account_id)
            if runtime is None:
                self._clear(where, "the account is gone")
                continue
            if runtime.recovery is not None and not runtime.recovery.finished.is_set():
                continue  # positions aren't rebuilt yet: an empty book would look flat
            open_qty = runtime.positions.net_qty(instrument_id)
            if open_qty == 0:
                self._clear(where, "the position is closed")
                continue
            if self._covered(runtime, instrument_id, open_qty):
                self._clear(where, "covered again (a stop algo, a stop order or a GTT)")
                continue
            side = "long" if open_qty > 0 else "short"
            self._alerts.raise_(
                key(*where),
                "ERROR",
                f"UNPROTECTED {account_id}: {abs(open_qty)} {instrument_id} ({side}) is open "
                f"with no stop: {why}. Start a stop for it, or close it.",
                account_id=account_id,
            )
            self._raised.add(where)

    # --- inside ---------------------------------------------------------------------------

    def _is_protection(self, account_id: str, code: str) -> bool:
        runtime = self._platform.accounts.get(account_id)
        if runtime is None:
            return False
        return any(s.code == code and s.family == PROTECTION for s in runtime.algos.specs())

    def _covered(self, runtime, instrument_id: str, open_qty: int) -> bool:
        closing = Side.SELL if open_qty > 0 else Side.BUY
        for algo in runtime.algos.algos():
            if (
                algo.instrument_id == instrument_id
                and algo.state is AlgoState.WORKING
                and self._is_protection(runtime.account.id, algo.code)
            ):
                return True
        for order in runtime.orders.orders():
            r = order.request
            if (
                order.is_working
                and r.instrument_id == instrument_id
                and r.side is closing
                and r.order_type in STOPS
            ):
                return True
        if runtime.gtt is not None:
            for gtt in runtime.gtt.list():
                if gtt.request.instrument_id == instrument_id and not gtt.status.is_final:
                    return True
        return False

    def _flag(self, where: tuple[str, str], why: str, algo: dict[str, Any] | None) -> None:
        """Remember a lost stop, durably: an UNPROTECTED note in the audit trail."""
        if self._flags.get(where) == why:
            return
        self._flags[where] = why
        if algo:
            self._lost[where] = algo
        self._note(where, FLAGGED, why, {"why": why, "algo": self._lost.get(where)})

    def _clear(self, where: tuple[str, str], how: str = "") -> None:
        if where in self._flags:
            self._flags.pop(where, None)
            self._lost.pop(where, None)
            self._note(where, RESOLVED, how or "resolved", {"how": how})
        if where in self._raised:
            self._raised.discard(where)
            self._alerts.clear(key(*where), "protected again, or the position is closed")

    def _note(self, where: tuple[str, str], kind: str, detail: str, extra: dict) -> None:
        log_ = getattr(self._platform, "event_log", None)
        if log_ is not None:
            log_.record_note(kind, where[0], f"{where[1]}: {detail}",
                             {"instrument_id": where[1], **extra})  # fmt: skip

    # --- restoring a lost stop (#264) -------------------------------------------------------

    def status(self, account_id: str) -> list[dict[str, Any]]:
        """The account's unprotected positions, for the screens: why, and whether the stop it
        had can be restored now (else what stands in the way)."""
        rows = []
        for (acc, instrument_id), why in sorted(self._flags.items()):
            if acc != account_id:
                continue
            runtime = self._platform.accounts.get(account_id)
            qty = runtime.positions.net_qty(instrument_id) if runtime is not None else 0
            algo = self._lost.get((acc, instrument_id))
            try:
                plan = self._plan(account_id, instrument_id)
                blocked, restore_as = None, plan["words"]
            except RestoreError as exc:
                blocked, restore_as = str(exc), None
            rows.append({"instrument_id": instrument_id, "qty": qty, "why": why,
                         "had": algo, "restore_as": restore_as, "blocked_by": blocked})  # fmt: skip
        return rows

    def restore(self, account_id: str, instrument_id: str, user_id: str) -> AlgoView:
        """A person confirmed: start the lost stop again for the position as it is now."""
        plan = self._plan(account_id, instrument_id)
        runtime = self._platform.accounts[account_id]
        view = runtime.algos.start(plan["code"], user_id=user_id, instrument_id=instrument_id,
                                   side=plan["side"], qty=plan["qty"], params=plan["params"],
                                   product=plan["product"])  # fmt: skip
        self._clear((account_id, instrument_id),
                    f"restored by {user_id} as {view.algo_id}: {plan['words']}")  # fmt: skip
        self.check()
        return view

    def _plan(self, account_id: str, instrument_id: str) -> dict[str, Any]:
        """What restoring would start, or why it can't now (RestoreError, in words)."""
        where = (account_id, instrument_id)
        runtime = self._platform.accounts.get(account_id)
        if runtime is None or where not in self._flags:
            raise RestoreError(f"{instrument_id} isn't flagged unprotected")
        algo = self._lost.get(where)
        if not algo:
            raise RestoreError("which stop it had isn't known: start a stop for it by hand")
        if runtime.recovery is not None and not runtime.recovery.finished.is_set():
            raise RestoreError("the account's state is still being rebuilt after the restart")
        product = Product(algo.get("product") or "NRML")
        held = _held(runtime, instrument_id, product)
        exit_side = Side(algo["side"])
        if algo["code"] == "BRACKET":  # its side is the entry's; its exits are the other way
            exit_side = Side.SELL if exit_side is Side.BUY else Side.BUY
        open_qty = held if exit_side is Side.SELL else -held
        if open_qty <= 0:
            raise RestoreError(f"no {product.value} position for a {exit_side.value} stop to "
                               "protect now: start a new stop by hand if you need one")  # fmt: skip
        unknown = getattr(self._platform, "unknown_size_of", lambda *_: None)(
            account_id, instrument_id, product)  # fmt: skip
        if unknown:
            raise RestoreError(f"the position's size isn't known ({unknown})")
        for a in runtime.algos.algos():
            if (a.instrument_id == instrument_id and not a.state.is_final
                    and self._is_protection(account_id, a.code)):  # fmt: skip
                raise RestoreError(f"{a.code} {a.algo_id} is still there ({a.state.value.lower()})"
                                   ": resume or stop it instead")  # fmt: skip
        live = {a.algo_id for a in runtime.algos.algos() if not a.state.is_final}
        for o in runtime.orders.orders():
            r = o.request
            if r.instrument_id != instrument_id or o.status.is_final:
                continue
            if o.pending is Pending.UNCONFIRMED:
                raise RestoreError(f"{o.client_order_id} hasn't been answered by the broker: "
                                   "wait until it settles")  # fmt: skip
            if r.parent_id is not None and r.parent_id not in live:
                raise RestoreError(f"{o.client_order_id} of the old stop is still working: "
                                   "wait until its cancel settles")  # fmt: skip
        if self._covered(runtime, instrument_id, held):
            raise RestoreError("it is covered again already")
        code, params = algo["code"], dict(algo.get("params") or {})
        qty = min(int(algo["qty"]), open_qty)
        if code == "BRACKET":
            avg = _avg(runtime, instrument_id, product)
            up = 1 if exit_side is Side.SELL else -1  # a long's target is above, its stop below
            target = avg + up * int(params["target_points_paise"])
            stop = avg - up * int(params["stop_points_paise"])
            code, params = "OCO", {"target_paise": target, "stop_paise": stop}
        words = f"{code} {exit_side.value} {qty} {instrument_id} {product.value} {params}"
        return {"code": code, "side": exit_side, "qty": qty, "params": params,
                "product": product, "words": words}  # fmt: skip


def _algo_record(a: AlgoView) -> dict[str, Any]:
    return {"algo_id": a.algo_id, "code": a.code, "side": a.side.value, "qty": a.qty,
            "params": dict(a.params), "product": a.product.value, "user_id": a.user_id}  # fmt: skip


def _held(runtime, instrument_id: str, product: Product) -> int:
    get = getattr(runtime.positions, "get", None)
    if get is None:
        return runtime.positions.net_qty(instrument_id)
    view = get(instrument_id, product)
    return view.qty if view is not None else 0


def _avg(runtime, instrument_id: str, product: Product) -> int:
    view = runtime.positions.get(instrument_id, product)
    return round(view.avg_price_paise)
