"""The spreader: trade a two-leg spread (e.g. buy this month, sell next month) as one order.

Buying the spread = buy leg A (the parent's instrument) and sell leg B, at A − B = the level.

    quote leg A at  level + B's best bid   (the price at which, selling B at its bid, A − B = level)
    B's price moves ─► re-quote A (within the modify budget: 24, then cancel and re-send)
    A fills         ─► at once, sell the same quantity of B at its best bid − pay-up ticks
    hedge done      ─► legging time and the spread actually traded are reported

Selling the spread mirrors it: sell A at level + B's best ask, then buy B at ask + pay-up.

Rules:
- The hedge never pays more than the trader's pay-up: if B runs away, the hedge rests at its
  limit and the algo says how much is unhedged; it never chases further on its own.
- Pause and stop pull the quote but keep hedges working (they reduce risk). A fill that
  crosses a cancel is still hedged.
- The algo is done only when both legs are complete. If it ends with any quantity unhedged
  (stopped, kill switch, hedge refused), it raises an alert naming the leg and quantity.
- Both legs must be tradable and use the same lot size (one A lot against one B lot).
"""

import math
from datetime import datetime

from laddrix.algos.engine import AlgoContext, BaseStrategy
from laddrix.algos.spec import AlgoSpec, ParamKind, ParamSpec
from laddrix.domain.orders import Side
from laddrix.domain.ticks import Tick
from laddrix.orders.model import OrderEvent, OrderView, Pending

MODIFY_BUDGET = 24  # brokers allow 25 modifications per order
MAX_LEG_SKEW_S = 3.0  # leg prices further apart in time than this: don't quote (#267)

SPEC = AlgoSpec(
    code="SPREAD",
    name="Spreader",
    help=(
        "Trades leg A against leg B at a spread level: quotes A, and when A fills, "
        "hedges B at once within your pay-up."
    ),
    params=(
        ParamSpec("leg2", "Leg B", ParamKind.INSTRUMENT, "The other contract (e.g. next month)"),
        ParamSpec(
            "level_paise",
            "Spread (A − B)",
            ParamKind.PRICE_OFFSET,
            "The price difference to trade at; may be negative",
        ),
        ParamSpec(
            "payup_ticks",
            "Pay-up",
            ParamKind.TICKS,
            "How many ticks worse than the best price the hedge may pay",
            default=2,
            min=0,
            max=50,
        ),
    ),
)


class Spreader(BaseStrategy):
    def __init__(self, params: dict) -> None:
        self.leg2: str = params["leg2"]
        self.level: int = params["level_paise"]
        self.payup: int = params["payup_ticks"]
        self.modifies: dict[str, int] = {}
        self.hedges: set[str] = set()  # leg B children seen so far (every leg B child is a hedge)
        self.hedge_in_flight = 0  # hedge quantity sent but not yet seen as a child
        self.first_unhedged_at: datetime | None = None
        self.legging_ms: list[float] = []
        self.stale = ""  # why the quote waits on old leg prices, in words

    # --- the engine's calls ---

    def start(self, ctx: AlgoContext) -> None:
        a, b = ctx.instrument(), ctx.instrument(self.leg2)
        if b is None:
            return ctx.fail(f"leg B {self.leg2} is not a known contract")
        if b.id == a.id:
            return ctx.fail("leg B must be a different contract from leg A")
        if not b.tradable:
            return ctx.fail(f"leg B {b.id} can't be traded")
        if b.lot_size != a.lot_size:
            return ctx.fail(f"the legs have different lot sizes ({a.lot_size} and {b.lot_size})")
        ctx.watch(self.leg2)
        self._report(ctx)
        self._quote(ctx)

    def on_tick(self, ctx: AlgoContext, tick: Tick) -> None:
        self._quote(ctx)

    def on_resume(self, ctx: AlgoContext) -> None:
        self._quote(ctx)

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status.is_final:
            self.modifies.pop(child.client_order_id, None)
        # Hedge on every child update, not only FILL events: a state change can report the
        # fill first (and a stop could otherwise end the algo before the FILL arrives).
        # Hedging is idempotent: it only covers what isn't covered yet.
        self._hedge(ctx)
        self._report(ctx)
        if not self._unhedged(ctx) and self.first_unhedged_at is not None:
            self.legging_ms.append((ctx.now() - self.first_unhedged_at).total_seconds() * 1000)
            self.first_unhedged_at = None
            self._report(ctx)

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        self._hedge(ctx)  # a hedge that couldn't go (no price yet) is retried

    def done(self, ctx: AlgoContext) -> bool:
        return ctx.filled(ctx.instrument_id) >= ctx.qty and self._unhedged(ctx) == 0

    def keep_on_stop(self, ctx: AlgoContext, child: OrderView) -> bool:
        return child.request.instrument_id == self.leg2  # hedges reduce risk: keep them

    def on_finish(self, ctx: AlgoContext) -> str | None:
        unhedged = self._unhedged(ctx)
        if unhedged == 0:
            return None
        leg = ctx.instrument_id if unhedged > 0 else self.leg2
        return (
            f"UNHEDGED: {abs(unhedged)} on {leg} (leg A filled {ctx.filled(ctx.instrument_id)}, "
            f"leg B filled {ctx.filled(self.leg2)}): hedge it by hand"
        )

    # --- inside ---

    def _unhedged(self, ctx: AlgoContext) -> int:
        """Leg A filled minus leg B filled (one B lot hedges one A lot)."""
        return ctx.filled(ctx.instrument_id) - ctx.filled(self.leg2)

    def _quote_price(self, ctx: AlgoContext) -> int | None:
        """The leg A price that makes A − B equal the level, against B's price on the side we'd
        hedge at; rounded so the spread is never worse than the level."""
        b = ctx.latest(self.leg2)
        if b is None:
            return None
        a_age, b_age = ctx.age_s(), ctx.age_s(self.leg2)
        if a_age is not None and b_age is not None and abs(a_age - b_age) > MAX_LEG_SKEW_S:
            self.stale = f"leg prices {abs(a_age - b_age):.0f} s apart"  # #267: one is stale
            return None
        self.stale = ""
        buying = ctx.side is Side.BUY
        hedge_level = b.best_bid if buying else b.best_ask  # we'd sell B (buying the spread)
        if hedge_level is None:
            return None
        raw = self.level + hedge_level.price_paise
        tick = ctx.instrument().tick_paise
        price = (math.floor(raw / tick) if buying else math.ceil(raw / tick)) * tick
        return price if price > 0 else None

    def _quote(self, ctx: AlgoContext) -> None:
        price = self._quote_price(ctx)
        quotes = [c for c in ctx.working() if c.request.instrument_id == ctx.instrument_id]
        if price is None:
            for q in quotes:
                ctx.cancel(q.client_order_id)  # no hedge price: don't leave a quote out
            ctx.say(f"waiting: {self.stale}" if self.stale
                    else "waiting: no price on leg B to hedge against")  # fmt: skip
            return
        for q in quotes:
            if q.request.price_paise == price or q.pending is not Pending.NONE:
                continue
            done = self.modifies.get(q.client_order_id, 0)
            if done >= MODIFY_BUDGET:
                ctx.cancel(q.client_order_id)
                continue
            self.modifies[q.client_order_id] = done + 1
            ctx.modify(q.client_order_id, price_paise=price)
        if not quotes:
            want = ctx.remaining()
            if want > 0 and self._unhedged(ctx) == 0 and ctx.send(want, price):
                ctx.say(f"quoting {want} of leg A at {price / 100:.2f}")

    def _hedge(self, ctx: AlgoContext) -> None:
        """Send leg B for whatever leg A filled that no hedge covers yet."""
        unhedged = self._unhedged(ctx)
        if unhedged <= 0:
            return
        if self.first_unhedged_at is None:
            self.first_unhedged_at = ctx.now()
        self._sync_hedges(ctx)
        covering = self.hedge_in_flight + sum(
            c.leaves_qty for c in ctx.working() if c.request.instrument_id == self.leg2
        )
        need = unhedged - covering
        if need <= 0:
            return
        b = ctx.latest(self.leg2)
        side = Side.SELL if ctx.side is Side.BUY else Side.BUY
        level = (b.best_bid if side is Side.SELL else b.best_ask) if b else None
        if level is None:
            ctx.say(f"UNHEDGED {unhedged}: no price on leg B yet")
            return
        tick = ctx.instrument(self.leg2).tick_paise
        price = level.price_paise + (-1 if side is Side.SELL else 1) * self.payup * tick
        sent = ctx.send(
            need, price, side=side, instrument_id=self.leg2, check_remaining=False, hedge=True
        )
        if sent:
            self.hedge_in_flight += need
            ctx.say(f"hedging {need} of leg B at {price / 100:.2f}")

    def _sync_hedges(self, ctx: AlgoContext) -> None:
        """A hedge that reached the order manager is no longer 'in flight'."""
        for c in ctx.children():
            if c.request.instrument_id == self.leg2 and c.client_order_id not in self.hedges:
                self.hedges.add(c.client_order_id)
                self.hedge_in_flight = max(0, self.hedge_in_flight - c.request.qty)

    def _report(self, ctx: AlgoContext) -> None:
        a_filled, b_filled = ctx.filled(ctx.instrument_id), ctx.filled(self.leg2)
        children = ctx.children()
        a_value = sum(
            c.filled_qty * c.avg_price_paise
            for c in children
            if c.request.instrument_id == ctx.instrument_id
        )
        b_value = sum(
            c.filled_qty * c.avg_price_paise
            for c in children
            if c.request.instrument_id == self.leg2
        )
        traded = round(a_value / a_filled - b_value / b_filled) if a_filled and b_filled else None
        ctx.report(
            leg2=self.leg2,
            level_paise=self.level,
            leg_a_filled=a_filled,
            leg_b_filled=b_filled,
            unhedged=a_filled - b_filled,
            traded_spread_paise=traded,
            max_legging_ms=round(max(self.legging_ms)) if self.legging_ms else None,
        )
