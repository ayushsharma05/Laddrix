"""POV (percentage of volume): trade a set share of what the market trades, until done.

    market volume since we started (the feed's day volume; our own fills are part of it)
        × the share  ─► what we should have filled by now
    every 2 seconds: send what's due (whole lots, at most the cap per child) at our side's
        best price (join) or the other side's (cross); keep working children at that price

A quiet market means a slow algo: it never trades ahead of the volume to finish sooner.
"""

from datetime import datetime, timedelta

from mocktrade.algos.engine import AlgoContext, BaseStrategy
from mocktrade.algos.execution import Chaser, lots, lots_text, touch
from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec
from mocktrade.orders.model import OrderEvent, OrderView

DECIDE_EVERY = timedelta(seconds=2)

SPEC = AlgoSpec(
    code="POV",
    name="Percent of volume",
    help="Trades a set share of the market's volume as it happens, until the order is done.",
    params=(
        ParamSpec(
            "pct", "Share", ParamKind.PERCENT, "Share of the market's volume", default=10,
            min=1, max=50,
        ),
        ParamSpec("max_lots", "Max per order", ParamKind.LOTS, "Largest child order", min=1),
        ParamSpec(
            "mode", "Price", ParamKind.CHOICE,
            "join: wait at the best price on our side; cross: take the other side",
            default="join", choices=("join", "cross"),
        ),
        ParamSpec(
            "limit_paise", "Limit", ParamKind.PRICE,
            "Never buy above / sell below this price (empty: no limit)", required=False, min=1,
        ),
    ),
)  # fmt: skip


class Pov(BaseStrategy):
    def __init__(self, params: dict) -> None:
        self.share = params["pct"] / 100
        self.max_lots = params["max_lots"]
        self.cross = params.get("mode", "join") == "cross"
        self.limit = params.get("limit_paise")
        self.chaser = Chaser()
        self.base: int | None = None  # the day volume when we started
        self.next_at: datetime | None = None

    def start(self, ctx: AlgoContext) -> None:
        self.next_at = ctx.now()
        self._baseline(ctx)

    def on_resume(self, ctx: AlgoContext) -> None:
        self.next_at = ctx.now()

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status.is_final:
            self.chaser.forget(child.client_order_id)

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        if self.next_at is None or now < self.next_at:
            return
        self.next_at = now + DECIDE_EVERY
        self.decide(ctx)

    def market_volume(self, ctx: AlgoContext) -> int | None:
        tick = ctx.latest()
        if tick is None or tick.volume is None or self.base is None:
            return None
        return max(0, tick.volume - self.base)

    def decide(self, ctx: AlgoContext) -> None:
        if self.base is None and not self._baseline(ctx):
            return
        traded = self.market_volume(ctx)
        if traded is None:
            return
        lot = ctx.instrument().lot_size
        filled = ctx.filled(ctx.instrument_id)
        working = sum(c.leaves_qty for c in ctx.working()) + ctx.sending
        due = lots(ctx, int(traded * self.share) - filled - working)
        share = round(100 * filled / traded, 1) if traded else 0
        ctx.report(market_qty=traded, our_share_pct=share)
        price = touch(ctx, cross=self.cross, limit=self.limit)
        if price is None:
            return
        self.chaser.chase(ctx, price)
        if due <= 0:
            ctx.say(f"keeping pace: {filled} of {traded} traded since start")
            return
        size = min(due, self.max_lots) * lot
        if ctx.send(min(size, ctx.remaining()), price):
            ctx.say(f"{lots_text(size // lot)} at {price / 100:.2f} · {filled} of {traded} traded")

    def _baseline(self, ctx: AlgoContext) -> bool:
        tick = ctx.latest()
        if tick is None or tick.volume is None:
            ctx.say("waiting: the feed gives no volume yet")
            return False
        self.base = tick.volume
        return True
