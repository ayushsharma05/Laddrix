"""Reconcile our positions with the broker's: any difference must be seen at once.

    every few minutes ─► broker.positions() ─► compare net qty per instrument
         differs once?  ─► check again soon (a fill may just be on its way)
         differs twice? ─► ALERT (screen + event log), until it matches again

Our positions come from our fills; the broker's come from the exchange. They must
agree. A real difference means a missed update, or a trade made outside MockTrade
(e.g. in the broker's own app), and a person must look. Nothing is "fixed"
automatically: silently adopting the broker's number would hide the bug behind it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.domain.instruments import InstrumentId

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mismatch:
    instrument_id: InstrumentId
    ours: int
    broker: int
    since: datetime  # when it was first seen

    @property
    def message(self) -> str:
        return (
            f"{self.instrument_id}: MockTrade shows {self.ours:+d}, the broker shows "
            f"{self.broker:+d} (a missed fill, or a trade made outside MockTrade)"
        )


@dataclass(frozen=True)
class CheckResult:
    ok: bool  # True when the broker answered and everything matches
    checked_at: datetime
    alerts: tuple[Mismatch, ...]  # confirmed differences (seen on two checks in a row)
    suspected: tuple[InstrumentId, ...]  # seen once: being re-checked soon
    error: str = ""  # the broker couldn't be asked


AlertListener = Callable[[str, Mismatch], None]  # ("raised" | "cleared", mismatch)


class PositionReconciler:
    def __init__(
        self,
        net_qty_by_instrument: Callable[[], dict[InstrumentId, int]],
        broker: Broker,
        *,
        account_id: str,
        interval_s: float = 180.0,
        recheck_s: float = 10.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.account_id = account_id
        self._ours = net_qty_by_instrument
        self._broker = broker
        self._interval_s = interval_s
        self._recheck_s = recheck_s
        self._clock = clock
        self._sleep = sleep
        self._suspected: dict[InstrumentId, datetime] = {}  # seen once, and when
        self._alerts: dict[InstrumentId, Mismatch] = {}
        self._listeners: list[AlertListener] = []
        self.last: CheckResult | None = None

    def alerts(self) -> list[Mismatch]:
        return list(self._alerts.values())

    def subscribe(self, listener: AlertListener) -> None:
        self._listeners.append(listener)

    async def check(self) -> CheckResult:
        """Compare once. Safe to call any time (e.g. right after a reconnect)."""
        now = self._clock()
        try:
            theirs = {p.instrument_id: p.qty for p in await self._broker.positions()}
        except BrokerError as exc:
            log.warning("couldn't reconcile positions: %s", exc.reason)
            self.last = CheckResult(False, now, tuple(self._alerts.values()), (), exc.reason)
            return self.last
        ours = self._ours()
        different = {
            instrument_id: (ours.get(instrument_id, 0), theirs.get(instrument_id, 0))
            for instrument_id in ours.keys() | theirs.keys()
            if ours.get(instrument_id, 0) != theirs.get(instrument_id, 0)
        }
        for instrument_id in list(self._alerts):
            if instrument_id not in different:
                self._clear(instrument_id)
        for instrument_id in list(self._suspected):
            if instrument_id not in different:
                del self._suspected[instrument_id]  # it was a fill on its way
        for instrument_id, (our_qty, broker_qty) in sorted(different.items()):
            current = self._alerts.get(instrument_id)
            if current is not None:
                if (current.ours, current.broker) != (our_qty, broker_qty):
                    self._raise(Mismatch(instrument_id, our_qty, broker_qty, current.since))
            elif instrument_id in self._suspected:
                since = self._suspected.pop(instrument_id)
                self._raise(Mismatch(instrument_id, our_qty, broker_qty, since))
            else:
                self._suspected[instrument_id] = now
        self.last = CheckResult(
            ok=not different,
            checked_at=now,
            alerts=tuple(self._alerts.values()),
            suspected=tuple(sorted(self._suspected)),
        )
        return self.last

    async def run(self) -> None:
        """Check forever: every interval, or soon after a first sighting. Never crashes."""
        while True:
            try:
                result = await self.check()
                wait = self._recheck_s if result.suspected else self._interval_s
            except Exception:
                log.exception("position reconcile failed")
                wait = self._interval_s
            await self._sleep(wait)

    def _raise(self, mismatch: Mismatch) -> None:
        self._alerts[mismatch.instrument_id] = mismatch
        log.error("position mismatch: %s", mismatch.message)
        self._notify("raised", mismatch)

    def _clear(self, instrument_id: InstrumentId) -> None:
        mismatch = self._alerts.pop(instrument_id)
        log.warning("position mismatch cleared: %s", instrument_id)
        self._notify("cleared", mismatch)

    def _notify(self, what: str, mismatch: Mismatch) -> None:
        for listener in list(self._listeners):
            try:
                listener(what, mismatch)
            except Exception:
                log.exception("reconcile listener failed")
