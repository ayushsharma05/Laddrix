"""TWAP and VWAP: an order worked to a schedule over a time window.

    the schedule says how much should be filled by now:
        TWAP: evenly over the window          VWAP: in step with the day's volume curve
    every decision (the interval, moved by up to ±20% so it isn't a clock others can see):
        behind by more than the trader allows ─► cross the spread for what's behind
        otherwise                             ─► rest what's due at our side's best price
    window over ─► "cross": take the rest at the other side's price (never past the limit)
                   "leave": stop, leaving the rest unfilled

The volume curve is data (minute weights from 09:15 IST), not code: today a typical NSE
U-shape (busy open and close, quiet lunch); later, the curve measured from recorded days.
A window outside market hours has no volume to follow, so VWAP follows time and says so.
A window running past the 15:30 close is cut at the close, and the algo says so.

VWAP also reports how it did: the market's VWAP over the same window (from the feed's
day volume and average price) and our average price against it, in basis points
(positive = we paid more / sold for less than the market).
"""

import math
import random
from datetime import datetime, time, timedelta
from itertools import accumulate

from laddrix.algos.engine import AlgoContext, BaseStrategy
from laddrix.algos.execution import Chaser, lots, lots_text, touch
from laddrix.algos.spec import AlgoSpec, ParamKind, ParamSpec
from laddrix.domain.orders import Side
from laddrix.options.model import IST
from laddrix.orders.model import OrderEvent, OrderView

OPEN, CLOSE = time(9, 15), time(15, 30)
SESSION_MIN = 375
JITTER = 0.2


class VolumeCurve:
    """How the day's volume is spread over its minutes (weights from 09:15 IST)."""

    def __init__(self, weights: list[float], open_: time = OPEN) -> None:
        if not weights or min(weights) < 0 or sum(weights) <= 0:
            raise ValueError("a volume curve needs non-negative weights adding up to more than 0")
        self.open = open_
        self.cum = [0.0, *accumulate(weights)]

    def upto(self, when: datetime) -> float:
        """Weight from the open to `when` (part of a minute counts in proportion)."""
        local = when.astimezone(IST)
        start = local.replace(hour=self.open.hour, minute=self.open.minute, second=0, microsecond=0)
        minutes = (local - start).total_seconds() / 60
        if minutes <= 0:
            return 0.0
        n = len(self.cum) - 1
        if minutes >= n:
            return self.cum[-1]
        whole = int(minutes)
        return self.cum[whole] + (self.cum[whole + 1] - self.cum[whole]) * (minutes - whole)

    def between(self, start: datetime, end: datetime) -> float:
        return self.upto(end) - self.upto(start)


def nse_u_shape() -> VolumeCurve:
    """A typical NSE day: about three times the lunch rate at the open, twice at the close."""
    return VolumeCurve(
        [
            1 + 2.0 * math.exp(-m / 25) + 1.2 * math.exp(-(SESSION_MIN - 1 - m) / 25)
            for m in range(SESSION_MIN)
        ]
    )


def window_params(extra: tuple[ParamSpec, ...] = ()) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(
            "duration_min", "Over (min)", ParamKind.MINUTES, "How long to work it", min=1, max=375
        ),
        ParamSpec(
            "limit_paise",
            "Limit",
            ParamKind.PRICE,
            "Never buy above / sell below this price (empty: no limit)",
            required=False,
            min=1,
        ),
        ParamSpec(
            "max_behind_pct",
            "Catch up at",
            ParamKind.PERCENT,
            "How far behind the schedule (share of the order) before it crosses the spread",
            default=10,
            min=1,
            max=100,
        ),
        ParamSpec(
            "at_end",
            "At the end",
            ParamKind.CHOICE,
            "cross: take what's left at the other side's price; leave: stop with it unfilled",
            default="cross",
            choices=("cross", "leave"),
        ),
        ParamSpec(
            "interval_s",
            "Decide every (s)",
            ParamKind.SECONDS,
            "How often it checks the schedule",
            default=30,
            min=5,
            max=600,
        ),
        *extra,
    )


TWAP_SPEC = AlgoSpec(
    code="TWAP",
    name="TWAP",
    help="Works the order evenly over a time window, resting passively; catches up when behind.",
    params=window_params(),
)

VWAP_SPEC = AlgoSpec(
    code="VWAP",
    name="VWAP",
    help="Works the order over a window in step with the day's volume curve, and reports "
    "its price against the market's VWAP.",
    params=window_params(),
)


def market_close(now: datetime) -> datetime | None:
    """Today's 15:30 IST if the market is open now, else None (nothing to cut at)."""
    local = now.astimezone(IST)
    if not OPEN <= local.time() < CLOSE or local.weekday() >= 5:
        return None
    return local.replace(hour=CLOSE.hour, minute=CLOSE.minute, second=0, microsecond=0)


class Scheduled(BaseStrategy):
    """Works toward `fraction(now)` of the order; TWAP and VWAP differ only in that."""

    def __init__(self, params: dict, rng: random.Random | None = None) -> None:
        self.duration = timedelta(minutes=params["duration_min"])
        self.limit = params.get("limit_paise")
        self.max_behind = params.get("max_behind_pct", 10) / 100
        self.cross_at_end = params.get("at_end", "cross") == "cross"
        self.interval = params.get("interval_s", 30)
        self.rng = rng or random.Random()
        self.chaser = Chaser()
        self.start_at = self.end_at = datetime.min
        self.next_at: datetime | None = None
        self.note = ""  # said along with the progress (e.g. cut at the close)

    def fraction(self, now: datetime) -> float:
        span = (self.end_at - self.start_at).total_seconds()
        return min(1.0, max(0.0, (now - self.start_at).total_seconds() / span))

    # --- the engine's calls ---

    def start(self, ctx: AlgoContext) -> None:
        self.start_at = ctx.now()
        self.end_at = self.start_at + self.duration
        close = market_close(self.start_at)
        if close is not None and self.end_at > close:
            self.end_at = close
            self.note = "window cut at the 15:30 close"
        if self.end_at <= self.start_at:
            return ctx.fail("the market closes before the window starts")
        ctx.report(ends_at=self.end_at.isoformat())
        self.next_at = self.start_at  # first decision on the next heartbeat

    def on_resume(self, ctx: AlgoContext) -> None:
        self.next_at = ctx.now()

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status.is_final:
            self.chaser.forget(child.client_order_id)

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        if self.next_at is None or now < self.next_at:
            return
        step = self.interval * (1 + self.rng.uniform(-JITTER, JITTER))
        self.next_at = now + timedelta(seconds=step)
        self.decide(ctx, now)

    # --- inside ---

    def decide(self, ctx: AlgoContext, now: datetime) -> None:
        lot = ctx.instrument().lot_size
        total = ctx.qty // lot
        frac = self.fraction(now)
        due = math.ceil(total * frac - 1e-9)  # lots that should be filled by now
        filled = lots(ctx, ctx.filled(ctx.instrument_id))
        working = lots(ctx, sum(c.leaves_qty for c in ctx.working()) + ctx.sending)
        self._report(ctx, frac, filled, total)
        if now >= self.end_at:
            if not self.cross_at_end:
                return ctx.finish(f"window over; {total - filled} lots left unfilled, as asked")
            urgent = True
        else:
            urgent = (due - filled) > self.max_behind * total
        price = touch(ctx, cross=urgent, limit=self.limit)
        if price is None:
            return
        self.chaser.chase(ctx, price)
        send = due - filled - working  # at the end, due is the whole order
        if send > 0 and ctx.send(min(send * lot, ctx.remaining()), price):
            how = "crossing to catch up" if urgent else "resting"
            what = f"{how}: {lots_text(send)} at {price / 100:.2f}"
            ctx.say(self._words(what, frac, filled, total))
        else:
            ctx.say(self._words("on schedule", frac, filled, total))

    def _words(self, what: str, frac: float, filled: int, total: int) -> str:
        words = f"{what} · {filled}/{total} lots, schedule {frac:.0%}"
        return f"{words} · {self.note}" if self.note else words

    def _report(self, ctx: AlgoContext, frac: float, filled: int, total: int) -> None:
        ctx.report(schedule_pct=round(100 * frac, 1))  # the screen shows what's filled


class Twap(Scheduled):
    pass


class Vwap(Scheduled):
    def __init__(
        self, params: dict, rng: random.Random | None = None, curve: VolumeCurve | None = None
    ) -> None:
        super().__init__(params, rng)
        self.curve = curve or nse_u_shape()
        self.window_weight = 0.0
        self.base: tuple[int, int] | None = None  # (day volume, day average price) at the start

    def start(self, ctx: AlgoContext) -> None:
        super().start(ctx)
        self.window_weight = self.curve.between(self.start_at, self.end_at)
        if self.window_weight <= 0:
            self.note = "outside market hours: following time, not volume"
        tick = ctx.latest()
        if tick is not None and tick.volume is not None and tick.avg_price_paise is not None:
            self.base = (tick.volume, tick.avg_price_paise)

    def fraction(self, now: datetime) -> float:
        if self.window_weight <= 0:
            return super().fraction(now)
        end = min(now, self.end_at)
        return min(1.0, max(0.0, self.curve.between(self.start_at, end) / self.window_weight))

    def _report(self, ctx: AlgoContext, frac: float, filled: int, total: int) -> None:
        super()._report(ctx, frac, filled, total)
        market = self.market_vwap(ctx)
        if market is None:
            return
        figures: dict = {"market_vwap_paise": market}
        ours = ctx.average_price()
        if ours is not None:
            sign = 1 if ctx.side is Side.BUY else -1
            figures["vs_vwap_bps"] = round(sign * (ours - market) / market * 1e4, 1)
        ctx.report(**figures)

    def market_vwap(self, ctx: AlgoContext) -> int | None:
        """The market's VWAP since we started, from the feed's day totals."""
        tick = ctx.latest()
        if self.base is None or tick is None or tick.volume is None or tick.avg_price_paise is None:
            return None
        v0, p0 = self.base
        traded = tick.volume - v0
        if traded <= 0:
            return None
        return round((tick.volume * tick.avg_price_paise - v0 * p0) / traded)
