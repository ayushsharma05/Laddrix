"""The iceberg: a big order shown a little at a time, at the trader's limit price.

    start              ─► show one slice (the display size) at the limit
    slice fully filled ─► show the next one at once
    variation          ─► each slice's size moves by up to ± that share of the display size,
                          in whole lots, so the refills don't make an obvious pattern
    slice cancelled, refused or pulled by a pause ─► the next heartbeat shows a new one

Only one slice is ever working: the market never sees more than the display size.
"""

import random
from datetime import datetime

from mocktrade.algos.engine import AlgoContext, BaseStrategy
from mocktrade.algos.execution import lots, lots_text
from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec
from mocktrade.domain.orders import OrderStatus
from mocktrade.orders.model import OrderEvent, OrderView

SPEC = AlgoSpec(
    code="ICEBERG",
    name="Iceberg",
    help="Shows only a small slice at your limit price; the next slice appears as each one fills.",
    params=(
        ParamSpec("display_lots", "Show (lots)", ParamKind.LOTS, "Lots visible at a time", min=1),
        ParamSpec("limit_paise", "Limit", ParamKind.PRICE, "The price every slice works at", min=1),
        ParamSpec(
            "variance_pct",
            "Vary size",
            ParamKind.PERCENT,
            "Change each slice's size by up to this share, so refills are harder to spot",
            default=0,
            min=0,
            max=50,
        ),
    ),
)


class Iceberg(BaseStrategy):
    def __init__(self, params: dict, rng: random.Random | None = None) -> None:
        self.display = params["display_lots"]
        self.limit = params["limit_paise"]
        self.variance = params.get("variance_pct", 0) / 100
        self.rng = rng or random.Random()
        self.shown = 0  # slices shown so far

    def start(self, ctx: AlgoContext) -> None:
        self._show(ctx)

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status is OrderStatus.FILLED:
            self._show(ctx)  # at once; a refused or cancelled slice waits for the heartbeat

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        self._show(ctx)  # after a refused or cancelled slice, or a resume

    def size(self, remaining_lots: int) -> int:
        """Lots for the next slice: the display size, varied, at least one, never too many."""
        swing = round(self.display * self.variance * self.rng.uniform(-1, 1))
        return max(1, min(self.display + swing, remaining_lots))

    def _show(self, ctx: AlgoContext) -> None:
        if ctx.working() or ctx.sending or ctx.held:
            return
        left = lots(ctx, ctx.remaining())
        if left <= 0:
            return
        size = self.size(left)
        if ctx.send(size * ctx.instrument().lot_size, self.limit):
            self.shown += 1
            ctx.say(f"showing {lots_text(size)} at {self.limit / 100:.2f} (slice {self.shown})")
            ctx.report(slices_shown=self.shown)
