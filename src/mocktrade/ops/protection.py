"""On every screen (the alert band): positions that had protection and have lost it (V1.2, #328).

A stop held by MockTrade (trailing stop, OCO, bracket) can stop protecting while its position
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
"""

from collections.abc import Iterable

from mocktrade.algos.engine import AlgoEvent, AlgoState
from mocktrade.domain.orders import OrderType, Side
from mocktrade.ops.alerts import Alerts

PROTECTION = "protection"
LOST_KINDS = ("PAUSED", "STOPPED", "FAILED", "DONE")  # DONE with a position open: its exit missed
COVERED_KINDS = ("STARTED", "RESUMED")
STOPS = (OrderType.SL, OrderType.SL_M)


def key(account_id: str, instrument_id: str) -> str:
    return f"unprotected:{account_id}:{instrument_id}"


class ProtectionGuard:
    def __init__(self, platform, alerts: Alerts) -> None:
        self._platform = platform
        self._alerts = alerts
        self._flags: dict[tuple[str, str], str] = {}  # (account, instrument) -> why, in words
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
        """Protection algos that were running when the server stopped: each one's position."""
        ids = self._platform.accounts if account_ids is None else account_ids
        for account_id in ids:
            for lost in self._platform.lost_protection(account_id):
                self._flags[(account_id, lost.instrument_id)] = (
                    f"its stop ({lost.words}) was running when the server stopped "
                    "and is not running now"
                )
        self.check()

    def on_algo(self, account_id: str, event: AlgoEvent) -> None:
        algo = event.algo
        if not self._is_protection(account_id, algo.code):
            return
        where = (account_id, algo.instrument_id)
        if event.kind in LOST_KINDS:
            why = f"its {algo.code} ({algo.algo_id}) {event.kind.lower()}"
            reason = event.detail or algo.reason
            self._flags[where] = f"{why}: {reason}" if reason else why
        elif event.kind in COVERED_KINDS:
            self._clear(where)  # the flag, and its band if one is showing
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
                self._clear(where)
                continue
            if runtime.recovery is not None and not runtime.recovery.finished.is_set():
                continue  # positions aren't rebuilt yet: an empty book would look flat
            open_qty = runtime.positions.net_qty(instrument_id)
            if open_qty == 0 or self._covered(runtime, instrument_id, open_qty):
                self._clear(where)
                continue
            side = "long" if open_qty > 0 else "short"
            self._alerts.raise_(
                key(*where),
                "ERROR",
                f"UNPROTECTED {account_id}: {abs(open_qty)} {instrument_id} ({side}) is open "
                f"with no stop: {why}. Start a stop for it, or close it.",
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

    def _clear(self, where: tuple[str, str]) -> None:
        self._flags.pop(where, None)
        if where in self._raised:
            self._raised.discard(where)
            self._alerts.clear(key(*where), "protected again, or the position is closed")
