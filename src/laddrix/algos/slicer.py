"""The slicer: a big order sent as small limit orders over time (time-sliced execution).

    every interval ─► send the next slice (whole lots, the remainder spread over the first)
                   ─► re-price unfilled slices to the current best price on our side
    a slice modified 24 times ─► cancel it and send its rest as a new order (brokers allow 25)
    the market beyond the trader's limit price ─► wait, and say so

Price modes:
- join:  wait at the best price on our side (buy at the best bid): cheaper, may not fill
- cross: take the best price on the other side (buy at the best ask): fills, pays the spread
"""

from datetime import datetime, timedelta

from laddrix.algos.engine import AlgoContext, BaseStrategy
from laddrix.algos.spec import AlgoSpec, ParamKind, ParamSpec
from laddrix.domain.orders import Side
from laddrix.domain.ticks import Tick
from laddrix.orders.model import OrderEvent, OrderView, Pending

MODIFY_BUDGET = 24  # one under the broker's 25, so a slice is never refused for it

SPEC = AlgoSpec(
    code="SLICER",
    name="Slicer",
    help="Sends the order as equal slices at regular times, keeping each slice at the best price.",
    params=(
        ParamSpec("slices", "Slices", ParamKind.COUNT, "How many pieces", min=1, max=200),
        ParamSpec(
            "interval_s", "Every (s)", ParamKind.SECONDS, "Time between slices", min=1, max=3600
        ),
        ParamSpec(
            "mode",
            "Price",
            ParamKind.CHOICE,
            "join: wait at the best price on our side; cross: take the other side",
            default="join",
            choices=("join", "cross"),
        ),
        ParamSpec(
            "limit_paise",
            "Limit",
            ParamKind.PRICE,
            "Never buy above / sell below this price (empty: no limit)",
            required=False,
            min=1,
        ),
    ),
)


def slice_sizes(total_lots: int, slices: int) -> list[int]:
    """Lots per slice: as even as whole lots allow, bigger ones first. 10 lots / 4 -> 3 3 2 2."""
    slices = max(1, min(slices, total_lots))
    base, extra = divmod(total_lots, slices)
    return [base + (1 if i < extra else 0) for i in range(slices)]


class Slicer(BaseStrategy):
    def __init__(self, params: dict) -> None:
        self.slices = params["slices"]
        self.interval = timedelta(seconds=params["interval_s"])
        self.cross = params.get("mode", "join") == "cross"
        self.limit = params.get("limit_paise")
        self.sizes: list[int] = []  # lots per slice, decided at start
        self.sent = 0  # slices sent
        self.next_at: datetime | None = None
        self.modifies: dict[str, int] = {}  # child id -> modifications we asked for

    # --- the engine's calls ---

    def start(self, ctx: AlgoContext) -> None:
        lot = ctx.instrument().lot_size
        self.sizes = slice_sizes(ctx.qty // lot, self.slices)
        self.next_at = ctx.now()
        self.on_timer(ctx, ctx.now())

    def on_resume(self, ctx: AlgoContext) -> None:
        self.next_at = ctx.now()  # paused slices were pulled: carry on at once

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        if self.next_at is None or now < self.next_at:
            return
        self.next_at = now + self.interval
        price = self._price(ctx)
        if price is None:
            return
        self._chase(ctx, price)
        self._send_slice(ctx, price)

    def on_tick(self, ctx: AlgoContext, tick: Tick) -> None:
        pass  # re-pricing happens on the slice clock, so a busy market doesn't burn modifications

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status.is_final:
            self.modifies.pop(child.client_order_id, None)

    # --- inside ---

    def _price(self, ctx: AlgoContext) -> int | None:
        tick = ctx.latest()
        if tick is None:
            ctx.say("waiting for a price")
            return None
        buying = ctx.side is Side.BUY
        level = (
            (tick.best_ask if self.cross else tick.best_bid)
            if buying
            else (tick.best_bid if self.cross else tick.best_ask)
        )
        if level is None:
            ctx.say("waiting: no price on the book")
            return None
        price = level.price_paise
        if self.limit is not None and (price > self.limit if buying else price < self.limit):
            ctx.say(f"waiting: the market is beyond the limit {self.limit / 100:.2f}")
            return None
        return price

    def _chase(self, ctx: AlgoContext, price: int) -> None:
        """Keep unfilled slices at the current best price; replace one that used its budget."""
        for child in ctx.working():
            if child.request.price_paise == price or child.pending is not Pending.NONE:
                continue
            done = self.modifies.get(child.client_order_id, 0)
            if done >= MODIFY_BUDGET:
                ctx.cancel(child.client_order_id)  # its rest goes out again in later slices
                continue
            self.modifies[child.client_order_id] = done + 1
            ctx.modify(child.client_order_id, price_paise=price)

    def _send_slice(self, ctx: AlgoContext, price: int) -> None:
        """One slice per interval, never bigger than planned: quantity that comes back
        (a refused or replaced slice) goes out in further slices after the plan, never as
        one big order."""
        lot = ctx.instrument().lot_size
        planned = self.sizes[self.sent] if self.sent < len(self.sizes) else self.sizes[0]
        qty = min(planned * lot, ctx.remaining())
        if qty > 0 and ctx.send(qty, price):
            self.sent += 1
            of = f" of {len(self.sizes)}" if self.sent <= len(self.sizes) else " (catching up)"
            ctx.say(f"slice {self.sent}{of} at {price / 100:.2f}")
