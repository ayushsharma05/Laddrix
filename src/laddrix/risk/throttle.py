"""The throttle: never send order messages faster than the broker and SEBI allow.

Why a sliding window and not a token bucket: a bucket of 8 that refills 8 per second
lets 8 orders out at 0.00 s and 7 more at 0.99 s, so 15 in one second. A sliding
window remembers when each recent message went out and allows a new one only if
fewer than the limit fall inside the window, so the limit holds for EVERY second.

    message ─► every rule that counts this kind has room? ─ yes ─► record it, go
                                │ no
               manual click: refused with "try again in 0.4 s"
               algo:         waits its turn (in order), up to max_wait_s
               cancel:       always waits; never refused (the kill switch must get out)
               daily cap:    refused (waiting won't help today)

Defaults sit below Kite's limits (10/s, 400/min, 5000/day per user, checked
14 Oct 2026) and SEBI's 10 orders per second for unregistered algos. New orders and
modifications may use 8 per second; cancels may use all 10, so cancels always have
room even while an algo is busy.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from enum import Enum

from laddrix.broker.rules import MAX_MODIFICATIONS_PER_ORDER
from laddrix.domain.orders import OrderRequest
from laddrix.orders.model import OrderView

IST = timezone(timedelta(hours=5, minutes=30))


class Kind(Enum):
    NEW = "new order"
    MODIFY = "modification"
    CANCEL = "cancel"


ALL_KINDS = frozenset(Kind)


@dataclass(frozen=True)
class RateRule:
    """At most `limit` messages per window (`cancel_limit` for cancels).

    `window_s=None` means a calendar day in India (resets at midnight IST)."""

    label: str  # how the limit is shown to the user, e.g. "per second"
    window_s: float | None
    limit: int  # for new orders and modifications
    cancel_limit: int  # cancels may go up to this; the gap is room reserved for cancels
    counts: frozenset[Kind] = ALL_KINDS

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.cancel_limit < self.limit:
            raise ValueError("limits must be positive and cancel_limit >= limit")
        if self.window_s is not None and self.window_s <= 0:
            raise ValueError("window must be positive")

    def limit_for(self, kind: Kind) -> int:
        return self.cancel_limit if kind is Kind.CANCEL else self.limit


DEFAULT_RULES = (
    RateRule("per second", 1.0, limit=8, cancel_limit=10),
    RateRule("per minute", 60.0, limit=350, cancel_limit=400),
    RateRule("per day", None, limit=4500, cancel_limit=4500, counts=frozenset({Kind.NEW})),
)


@dataclass(frozen=True)
class Usage:
    """How much of one limit is used right now, for a meter on screen."""

    label: str
    used: int
    limit: int


@dataclass
class _Counter:
    rule: RateRule
    sent: deque[float] = field(default_factory=deque)  # send times inside the window
    day: date | None = None
    day_count: int = 0

    def used(self, now: float, today: date) -> int:
        if self.rule.window_s is None:
            return self.day_count if self.day == today else 0
        cutoff = now - self.rule.window_s
        while self.sent and self.sent[0] <= cutoff:
            self.sent.popleft()
        return len(self.sent)

    def wait_s(self, now: float, today: date, limit: int) -> float | None:
        """Seconds until one more message fits; 0 if it fits now; None if not today."""
        used = self.used(now, today)
        if used < limit:
            return 0.0
        if self.rule.window_s is None:
            return None
        # the (used - limit + 1)-th oldest send has to leave the window first
        leaving = self.sent[used - limit]
        return leaving + self.rule.window_s - now

    def record(self, now: float, today: date) -> None:
        if self.rule.window_s is None:
            if self.day != today:
                self.day, self.day_count = today, 0
            self.day_count += 1
        else:
            self.sent.append(now)


class OrderThrottle:
    """An order gate (see orders.gate) for one broker account."""

    def __init__(
        self,
        rules: tuple[RateRule, ...] = DEFAULT_RULES,
        *,
        max_wait_s: float = 5.0,
        monotonic: Callable[[], float] | None = None,
        today: Callable[[], date] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        broker_limits: str = "Kite allows 10 per second, 400 per minute, 5000 per day",
    ) -> None:
        self._now = monotonic or time.monotonic
        self._today = today or (lambda: datetime.now(UTC).astimezone(IST).date())
        self._sleep = sleep
        self._max_wait_s = max_wait_s
        self._counters = [_Counter(rule) for rule in rules]
        self._broker_limits = broker_limits
        # the most new orders allowed in one second (the SEBI line for algos is 10, #291)
        self.per_second = min((r.limit for r in rules if r.window_s == 1.0), default=0)
        self._waiting = {"orders": asyncio.Lock(), "cancels": asyncio.Lock()}
        self._modifications: dict[str, int] = {}
        self._modifications_day: date | None = None

    # --- the gate ---------------------------------------------------------------------------

    async def admit_new(self, request: OrderRequest) -> str | None:
        return await self._admit(Kind.NEW, waits=request.from_algo)

    async def admit_modify(self, order: OrderView, new: OrderRequest) -> str | None:
        self._reset_modifications_if_new_day()
        done = self._modifications.get(order.client_order_id, 0)
        if done >= MAX_MODIFICATIONS_PER_ORDER:
            return (
                f"this order has been modified {done} times, the most allowed: "
                "cancel it and place a new one"
            )
        reason = await self._admit(Kind.MODIFY, waits=new.from_algo)
        if reason is None:  # counted when sent, even if the broker then refuses: errs safe
            self._modifications[order.client_order_id] = done + 1
        return reason

    async def admit_cancel(self, order: OrderView) -> str | None:
        return await self._admit(Kind.CANCEL, waits=True)

    # --- for screens ------------------------------------------------------------------------

    def usage(self) -> list[Usage]:
        now, today = self._now(), self._today()
        return [Usage(c.rule.label, c.used(now, today), c.rule.limit) for c in self._counters]

    # --- inside -----------------------------------------------------------------------------

    async def _admit(self, kind: Kind, *, waits: bool) -> str | None:
        lane = self._waiting["cancels" if kind is Kind.CANCEL else "orders"]
        if not (waits and lane.locked()):  # never jump ahead of messages already waiting
            reason, wait = self._try(kind)
            if reason is None or not waits:
                return reason  # manual clicks get an answer at once (and go ahead of algos)
            if wait is None:  # a daily cap: waiting can't help
                return reason
        async with lane:  # waiters go out in the order they arrived
            waited = 0.0
            while True:
                reason, wait = self._try(kind)
                if reason is None or wait is None:
                    return reason
                if kind is not Kind.CANCEL and waited + wait > self._max_wait_s:
                    return f"{reason} (waited {waited:.1f} s, the most an algo order may wait)"
                await self._sleep(wait)
                waited += wait

    def _try(self, kind: Kind) -> tuple[str | None, float | None]:
        """Record the message and return (None, 0) if it fits; else (reason, wait in s)."""
        now, today = self._now(), self._today()
        longest, blocking = 0.0, None
        for counter in self._counters:
            if kind not in counter.rule.counts:
                continue
            wait = counter.wait_s(now, today, counter.rule.limit_for(kind))
            if wait is None:
                return self._daily_reason(counter.rule, kind), None
            if wait > longest:
                longest, blocking = wait, counter.rule
        if blocking is not None:
            limit = blocking.limit_for(kind)
            return (
                f"order rate limit: {limit} {kind.value}s {blocking.label} reached "
                f"({self._broker_limits}); try again in {_round_up(longest)} s"
            ), longest
        for counter in self._counters:
            if kind in counter.rule.counts:
                counter.record(now, today)
        return None, 0.0

    def _daily_reason(self, rule: RateRule, kind: Kind) -> str:
        return f"daily limit of {rule.limit_for(kind)} {kind.value}s reached; it resets at midnight"

    def _reset_modifications_if_new_day(self) -> None:
        today = self._today()
        if self._modifications_day != today:
            self._modifications_day, self._modifications = today, {}


def _round_up(seconds: float) -> str:
    """Shown wait times round UP, so retrying after the shown time always works."""
    return f"{math.ceil(round(seconds, 6) * 10) / 10:.1f}"
