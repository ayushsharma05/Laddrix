"""Surveillance alerts for the risk role (#291): patterns an exchange or a regulator would ask
about, worked out on the server as orders happen.

    each order event ─► possible self-trade: a new order that could match a working order of
                         the same account or the same user on the other side
                     ─► an unusually large order: more than `large_x` times the account's
                         median order size today (after `large_after` orders)
                     ─► a high cancel/modify ratio: cancels and changes at least `churn_x`
                         times the orders placed (after `churn_after` orders)
                     ─► a burst: `burst_per_s` orders or more within one second, near the
                         exchange's 10 orders a second for retail algos

Each alert is kept in the audit trail (SURVEILLANCE), shown to risk users, and acknowledged
by one of them with a note (SURVEILLANCE_ACK). An alert never blocks an order: the risk
checks do that; this tells a person something deserves a look. Each kind is raised at most
once per account and instrument a day (a burst: once a minute), so a busy day stays readable.

Thresholds are settings, checked against SEBI's retail algo framework (10 orders a second per
exchange before an algo needs exchange registration) on 23 Oct 2026; re-check before going
live. docs/design/compliance.md.
"""

import itertools
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from laddrix.domain.orders import OrderType, Side
from laddrix.orders.model import EventKind, OrderEvent

log = logging.getLogger(__name__)

SELF_TRADE = "self_trade"
LARGE = "large_order"
CHURN = "cancel_ratio"
BURST = "burst"


@dataclass(frozen=True)
class SurveillanceLimits:
    large_x: float = 5.0  # an order this many times the day's median size
    large_after: int = 10  # orders today before "unusual" means anything
    churn_x: float = 3.0  # cancels + changes this many times the orders placed
    churn_after: int = 10
    burst_per_s: int = 8  # orders within one second (the exchange's line is 10)


@dataclass(frozen=True)
class SurveillanceAlert:
    alert_id: str
    kind: str
    account_id: str
    instrument_id: str
    words: str
    at: datetime
    acked_by: str = ""
    ack_note: str = ""
    acked_at: datetime | None = None


def alert_to_dict(a: SurveillanceAlert) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id, "kind": a.kind, "account_id": a.account_id,
        "instrument_id": a.instrument_id, "words": a.words, "at": a.at.isoformat(),
        "acked_by": a.acked_by, "ack_note": a.ack_note,
        "acked_at": a.acked_at.isoformat() if a.acked_at else None,
    }  # fmt: skip


@dataclass
class _Working:
    account_id: str
    user_id: str
    instrument_id: str
    side: Side
    price: int | None  # None: a market order (it would match anything)


@dataclass
class _Day:
    sizes: list[int] = field(default_factory=list)
    placed: int = 0
    churn: int = 0  # cancels and changes sent
    recent: list[datetime] = field(default_factory=list)


class Surveillance:
    """Firm-wide: every account's order events (see the module notes)."""

    def __init__(
        self,
        *,
        limits: SurveillanceLimits | None = None,
        note: Callable[[str, str, str, dict], None] = lambda _k, _a, _d, _x: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.limits = limits or SurveillanceLimits()
        self._note = note  # (kind, account_id, words, extra): the audit trail
        self._clock = clock
        self._alerts: dict[str, SurveillanceAlert] = {}
        self._ids = itertools.count(1)
        self._working: dict[str, _Working] = {}
        self._days: dict[tuple[str, object], _Day] = defaultdict(_Day)
        self._raised: dict[tuple, datetime] = {}  # (kind, account, instrument, day) -> when

    # --- reading and acknowledging ---------------------------------------------------------------

    def alerts(self, *, open_only: bool = False) -> list[SurveillanceAlert]:
        found = sorted(self._alerts.values(), key=lambda a: a.at)
        return [a for a in found if not (open_only and a.acked_by)]

    def acknowledge(self, alert_id: str, by: str, note: str) -> SurveillanceAlert:
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise KeyError(f"no surveillance alert {alert_id}")
        if len(note.strip()) < 5:
            raise ValueError("say what you found (at least 5 characters): it is kept")
        if alert.acked_by:
            raise ValueError(f"already acknowledged by {alert.acked_by}")
        done = replace(alert, acked_by=by, ack_note=note.strip(), acked_at=self._clock())
        self._alerts[alert_id] = done
        self._note("SURVEILLANCE_ACK", alert.account_id,
                   f"{alert_id} acknowledged by {by}: {note.strip()}",
                   {"alert_id": alert_id, "by": by, "note": note.strip()})  # fmt: skip
        return done

    def restore(self, raised: list[dict], acks: list[dict]) -> None:
        """After a restart: today's alerts and acknowledgements from the audit trail."""
        for x in raised:
            a = SurveillanceAlert(x["alert_id"], x["kind"], x["account_id"],
                                  x.get("instrument_id", ""), x["words"],
                                  datetime.fromisoformat(x["at"]))  # fmt: skip
            self._alerts[a.alert_id] = a
            day = a.at.date()
            self._raised[(a.kind, a.account_id, a.instrument_id, day)] = a.at
        for x in acks:
            seen = self._alerts.get(x["alert_id"])
            if seen is not None:
                self._alerts[seen.alert_id] = replace(seen, acked_by=x["by"], ack_note=x["note"])
        numbers = [int(i[1:]) for i in self._alerts if i[1:].isdigit()]
        self._ids = itertools.count(max(numbers, default=0) + 1)

    # --- watching ------------------------------------------------------------------------------

    def on_order_event(self, event: OrderEvent) -> None:
        """Order manager listener (every account's)."""
        try:
            self._on(event)
        except Exception:  # never let a report break trading
            log.exception("surveillance failed on %s", event.client_order_id)

    def _on(self, event: OrderEvent) -> None:
        view = event.order
        if view is None:
            return
        r, cid = view.request, event.client_order_id
        day = self._days[(event.account_id, event.ts.date())]
        if event.kind is EventKind.SUBMITTED:
            self._self_trade(event, r)
            self._working[cid] = _Working(event.account_id, r.user_id, r.instrument_id, r.side,
                                          None if r.order_type is OrderType.MARKET
                                          else r.price_paise)  # fmt: skip
            self._large(event, day, r.qty)
            day.placed += 1
            self._burst(event, day)
        elif event.kind in (EventKind.MODIFY_SENT, EventKind.CANCEL_SENT):
            day.churn += 1
            self._churn(event, day)
            if event.kind is EventKind.MODIFY_SENT and cid in self._working:
                self._working[cid].price = r.price_paise
        if not view.is_working:
            self._working.pop(cid, None)

    def _self_trade(self, event: OrderEvent, r) -> None:
        mine = None if r.order_type is OrderType.MARKET else r.price_paise
        for cid, w in self._working.items():
            if w.instrument_id != r.instrument_id or w.side is r.side:
                continue
            if w.account_id != event.account_id and w.user_id != r.user_id:
                continue
            buy, sell = (mine, w.price) if r.side is Side.BUY else (w.price, mine)
            if buy is None or sell is None or buy >= sell:
                who = "the same account" if w.account_id == event.account_id else "the same user"
                self._raise(SELF_TRADE, event.account_id, r.instrument_id,
                            f"possible self-trade: {r.side.value} {r.qty} {r.instrument_id} "
                            f"({event.client_order_id}) could match {w.side.value} order {cid} "
                            f"of {who}")  # fmt: skip
                return

    def _large(self, event: OrderEvent, day: _Day, qty: int) -> None:
        lim = self.limits
        if len(day.sizes) >= lim.large_after:
            usual = median(day.sizes)
            if qty > lim.large_x * usual and event.order is not None:
                r = event.order.request
                self._raise(LARGE, event.account_id, r.instrument_id,
                            f"unusually large order: {r.side.value} {qty} {r.instrument_id} "
                            f"({event.client_order_id}), {qty / usual:.1f}x this account's "
                            f"median of {usual:g} today")  # fmt: skip
        day.sizes.append(qty)

    def _churn(self, event: OrderEvent, day: _Day) -> None:
        lim = self.limits
        if day.placed >= lim.churn_after and day.churn >= lim.churn_x * day.placed:
            self._raise(CHURN, event.account_id, "",
                        f"high cancel/modify ratio: {day.churn} cancels and changes for "
                        f"{day.placed} orders today ({day.churn / day.placed:.1f}x)")  # fmt: skip

    def _burst(self, event: OrderEvent, day: _Day) -> None:
        now = event.ts
        day.recent = [t for t in day.recent if now - t < timedelta(seconds=1)] + [now]
        if len(day.recent) >= self.limits.burst_per_s:
            words = (f"{len(day.recent)} orders within one second (the exchange's line for "
                     "algos without registration is 10 a second)")  # fmt: skip
            self._raise(BURST, event.account_id, "", words, every=timedelta(minutes=1))

    def _raise(self, kind: str, account_id: str, instrument_id: str, words: str,
               every: timedelta | None = None) -> None:  # fmt: skip
        now = self._clock()
        key = (kind, account_id, instrument_id, now.date())
        last = self._raised.get(key)
        if last is not None and (every is None or now - last < every):
            return
        self._raised[key] = now
        alert = SurveillanceAlert(f"S{next(self._ids)}", kind, account_id, instrument_id, words,
                                  now)  # fmt: skip
        self._alerts[alert.alert_id] = alert
        log.warning("surveillance %s: %s", account_id, words)
        self._note("SURVEILLANCE", account_id, words, {**alert_to_dict(alert)})
