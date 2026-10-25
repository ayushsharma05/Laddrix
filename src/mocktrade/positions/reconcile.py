"""Reconcile our positions with the broker's: any difference must be seen at once.

    every few minutes ─► broker.positions() ─► compare qty per instrument AND product
         differs once?  ─► check again soon (a fill may just be on its way)
         differs twice? ─► ALERT (screen + event log), until it matches again

Our positions come from our fills; the broker's come from the exchange. They must
agree. A real difference means a missed update, or a trade made outside MockTrade
(e.g. in the broker's own app), and a person must look. Nothing is "fixed"
automatically: silently adopting the broker's number would hide the bug behind it.

Positions are compared per instrument and product (MIS, NRML, CNC): Kite lists one row for
each, and the same contract can be long in one and short in another (#258: comparing per
instrument let one row overwrite the other). Where the broker doesn't say a row's product, that
instrument is compared on its net across products instead.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mocktrade.broker.base import Broker, BrokerError, BrokerPosition
from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.orders import Product

log = logging.getLogger(__name__)


Key = tuple[InstrumentId, Product | None]  # None: net across products


@dataclass(frozen=True)
class Mismatch:
    instrument_id: InstrumentId
    ours: int
    broker: int
    since: datetime  # when it was first seen
    product: Product | None = None  # None: the net across products differs

    @property
    def key(self) -> Key:
        return (self.instrument_id, self.product)

    @property
    def message(self) -> str:
        what = f"{self.instrument_id} {self.product.value}" if self.product else self.instrument_id
        return (
            f"{what}: MockTrade shows {self.ours:+d}, the broker shows "
            f"{self.broker:+d} (a missed fill, or a trade made outside MockTrade)"
        )


def differences(
    ours: dict[tuple[InstrumentId, Product], int], theirs: list[BrokerPosition]
) -> dict[Key, tuple[int, int]]:
    """Where our positions and the broker's differ: (ours, broker) per instrument and product,
    or per instrument (product None) where the broker didn't say a row's product."""
    mine: dict[Key, int] = {k: q for k, q in ours.items()}  # the broker's key shape
    broker: dict[Key, int] = {}
    unsure: set[InstrumentId] = set()
    for p in theirs:
        if p.product is None:
            unsure.add(p.instrument_id)
        key = (p.instrument_id, p.product)
        broker[key] = broker.get(key, 0) + p.qty  # never overwritten: rows add up
    out: dict[Key, tuple[int, int]] = {}
    for instrument_id in unsure:  # compared on the net, both sides
        a = sum(q for (i, _), q in ours.items() if i == instrument_id)
        b = sum(q for (i, _), q in broker.items() if i == instrument_id)
        if a != b:
            out[(instrument_id, None)] = (a, b)
    keys = {k for k in ours if k[0] not in unsure} | {k for k in broker if k[0] not in unsure}
    for key in keys:
        a, b = mine.get(key, 0), broker.get(key, 0)
        if a != b:
            out[key] = (a, b)
    return out


@dataclass(frozen=True)
class CheckResult:
    ok: bool  # True when the broker answered and everything matches
    checked_at: datetime
    alerts: tuple[Mismatch, ...]  # confirmed differences (seen on two checks in a row)
    suspected: tuple[Key, ...]  # seen once: being re-checked soon
    error: str = ""  # the broker couldn't be asked


AlertListener = Callable[[str, Mismatch], None]  # ("raised" | "cleared", mismatch)


class PositionReconciler:
    def __init__(
        self,
        qty_by_product: Callable[[], dict[tuple[InstrumentId, Product], int]],
        broker: Broker,
        *,
        account_id: str,
        interval_s: float = 180.0,
        recheck_s: float = 10.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.account_id = account_id
        self._ours = qty_by_product
        self._broker = broker
        self._interval_s = interval_s
        self._recheck_s = recheck_s
        self._clock = clock
        self._sleep = sleep
        self._suspected: dict[Key, datetime] = {}  # seen once, and when
        self._alerts: dict[Key, Mismatch] = {}
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
            theirs = await self._broker.positions()
        except BrokerError as exc:
            log.warning("couldn't reconcile positions: %s", exc.reason)
            self.last = CheckResult(False, now, tuple(self._alerts.values()), (), exc.reason)
            return self.last
        different = differences(self._ours(), theirs)
        for key in list(self._alerts):
            if key not in different:
                self._clear(key)
        for key in list(self._suspected):
            if key not in different:
                del self._suspected[key]  # it was a fill on its way
        for key, (our_qty, broker_qty) in sorted(
            different.items(), key=lambda kv: _key_order(kv[0])
        ):
            instrument_id, product = key
            current = self._alerts.get(key)
            if current is not None:
                if (current.ours, current.broker) != (our_qty, broker_qty):
                    self._raise(Mismatch(instrument_id, our_qty, broker_qty, current.since,
                                         product))  # fmt: skip
            elif key in self._suspected:
                since = self._suspected.pop(key)
                self._raise(Mismatch(instrument_id, our_qty, broker_qty, since, product))
            else:
                self._suspected[key] = now
        self.last = CheckResult(
            ok=not different,
            checked_at=now,
            alerts=tuple(self._alerts.values()),
            suspected=tuple(sorted(self._suspected, key=_key_order)),
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
        self._alerts[mismatch.key] = mismatch
        log.error("position mismatch: %s", mismatch.message)
        self._notify("raised", mismatch)

    def _clear(self, key: Key) -> None:
        mismatch = self._alerts.pop(key)
        log.warning("position mismatch cleared: %s %s", *key)
        self._notify("cleared", mismatch)

    def _notify(self, what: str, mismatch: Mismatch) -> None:
        for listener in list(self._listeners):
            try:
                listener(what, mismatch)
            except Exception:
                log.exception("reconcile listener failed")


def _key_order(key: Key) -> tuple[str, str]:
    return (key[0], key[1].value if key[1] else "")
