"""What execution algos share: where to price a child, and keeping children at that price.

touch(ctx, cross, limit) -> the price to work at now, or None (and the algo says why)
    join:  our side's best price (buy at the best bid): cheaper, may not fill
    cross: the other side's best price (buy at the best ask): fills, pays the spread
    never beyond the trader's limit (buy above / sell below it)
Chaser.chase(ctx, price) -> working children moved to the price; a child that used its
    modification budget is cancelled instead (its quantity goes out again later)
"""

from laddrix.algos.engine import AlgoContext
from laddrix.domain.orders import Side
from laddrix.orders.model import Pending

MODIFY_BUDGET = 24  # one under the broker's 25, so a child is never refused for it


def beyond(side: Side, price: int, limit: int | None) -> bool:
    """True if trading at `price` would break the trader's limit."""
    if limit is None:
        return False
    return price > limit if side is Side.BUY else price < limit


def touch(ctx: AlgoContext, *, cross: bool, limit: int | None = None) -> int | None:
    tick = ctx.latest()
    if tick is None:
        ctx.say("waiting for a price")
        return None
    buying = ctx.side is Side.BUY
    level = (
        (tick.best_ask if cross else tick.best_bid)
        if buying
        else (tick.best_bid if cross else tick.best_ask)
    )
    if level is None:
        ctx.say("waiting: no price on the book")
        return None
    if beyond(ctx.side, level.price_paise, limit):
        assert limit is not None
        ctx.say(f"waiting: the market is beyond the limit {limit / 100:.2f}")
        return None
    return level.price_paise


class Chaser:
    """Moves working children to a new price, counting each child's modifications."""

    def __init__(self) -> None:
        self.modifies: dict[str, int] = {}

    def chase(self, ctx: AlgoContext, price: int) -> None:
        for child in ctx.working():
            if child.request.price_paise == price or child.pending is not Pending.NONE:
                continue
            done = self.modifies.get(child.client_order_id, 0)
            if done >= MODIFY_BUDGET:
                ctx.cancel(child.client_order_id)
                continue
            self.modifies[child.client_order_id] = done + 1
            ctx.modify(child.client_order_id, price_paise=price)

    def forget(self, client_order_id: str) -> None:
        self.modifies.pop(client_order_id, None)


def lots(ctx: AlgoContext, qty: int) -> int:
    """Whole lots in a quantity (rounded down)."""
    return qty // ctx.instrument().lot_size


def lots_text(n: int) -> str:
    return f"{n} lot" if n == 1 else f"{n} lots"
