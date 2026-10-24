"""The Autospreader algo (ASE, #415): trade a multi-leg spread at a spread price.

It works one of the trader's spreads (spreads/model.py), copied when it starts: editing the
definition later never changes an algo already running.

    the parent order       the quoting leg Q: its instrument, its side, its quantity
                           (spread lots · |Q's ratio| · Q's lot size)
    quote Q at the price that makes the spread = P against the other legs' prices now:
        Q's price = (P − Σ others' price× · the price they'd hedge at) / Q's price×
        rounded so the spread is never worse than P (buying: never above; selling: below)
    the other legs move ─► re-quote Q (within the modify budget: 24, then cancel and re-send)
    Q fills f lots      ─► at once, every other leg: floor(f · |its ratio| / |Q's ratio|) lots,
                           at its best price ± its pay-up ticks (a limit: never chases further)
    every leg complete  ─► DONE; the spread actually traded (Σ price× · average) is reported

Buying the spread buys the + legs and sells the − legs; selling it mirrors that.

Rules (the two-leg spreader's, for N legs):
- Pause and stop pull the quote but keep hedges working: they reduce risk.
- A fill that crosses a cancel is still hedged. Hedging is idempotent: it only sends what no
  fill or working hedge covers yet, so a repeated or out-of-order update never over-hedges.
- No new quote while any leg is unhedged, while a leg has no price, or while the legs'
  prices are more than MAX_LEG_SKEW_S apart (one is stale, #267).
- The algo ends only with every leg complete. Ending with any leg short (stopped, a kill
  switch, a hedge refused) raises an alert naming each leg and quantity.
"""

import math
from collections.abc import Callable
from datetime import datetime
from fractions import Fraction

from mocktrade.algos.engine import AlgoContext, AlgoType, BaseStrategy
from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec
from mocktrade.domain.orders import Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.orders.model import OrderEvent, OrderView, Pending
from mocktrade.spreads.model import MAX_LEG_SKEW_S, SpreadDef, SpreadLeg

MODIFY_BUDGET = 24  # brokers allow 25 modifications per order

SPEC = AlgoSpec(
    code="ASE",
    name="Autospreader",
    help=(
        "Trades one of your spreads at a spread price: quotes its quoting leg and, when it "
        "fills, hedges every other leg at once within each leg's pay-up."
    ),
    params=(
        ParamSpec("spread", "Spread", ParamKind.SPREAD, "One of your spreads (Autospreader)"),
        ParamSpec(
            "level_paise",
            "Spread price",
            ParamKind.PRICE_OFFSET,
            "The spread price to trade at; may be negative",
        ),  # fmt: skip
    ),
    family="spread",
)


def algo_type(lookup: Callable[[str], SpreadDef | None]) -> AlgoType:
    """The ASE algo, finding spreads with `lookup` (the server's SpreadBook.get)."""
    return AlgoType(SPEC, lambda params: AutoSpreader(params, lookup))


class AutoSpreader(BaseStrategy):
    def __init__(self, params: dict, lookup: Callable[[str], SpreadDef | None]) -> None:
        spread = lookup(params["spread"])
        if spread is None:
            raise ValueError(f"there is no spread {params['spread']}")
        self.spread = spread  # a copy: the definition can change, this algo doesn't
        self.level: int = params["level_paise"]
        self.q: SpreadLeg = spread.quote_leg
        self.others: tuple[SpreadLeg, ...] = tuple(x for x in spread.legs if not x.quote)
        self.buying = True  # buying the spread (set at start, from the parent's side)
        self.modifies: dict[str, int] = {}
        self.in_flight: dict[str, int] = {leg.instrument_id: 0 for leg in self.others}
        self.seen: set[str] = set()  # hedge children already counted out of `in_flight`
        self.first_unhedged_at: datetime | None = None
        self.legging_ms: list[float] = []
        self.stale = ""

    # --- the engine's calls ---

    def start(self, ctx: AlgoContext) -> None:
        if self.spread.user_id != ctx.user_id:
            return ctx.fail(f"{self.spread.name} is not your spread")
        if ctx.instrument_id != self.q.instrument_id:
            return ctx.fail(f"the quoting leg is {self.q.instrument_id}, not {ctx.instrument_id}")
        lot = ctx.instrument().lot_size
        if ctx.qty % (abs(self.q.ratio) * lot):
            return ctx.fail(f"{ctx.qty} is not a whole number of spread lots "
                            f"({abs(self.q.ratio)} × {lot} of the quoting leg each)")  # fmt: skip
        # the parent buys Q when buying the spread if Q's ratio is +, sells it if −
        self.buying = (ctx.side is Side.BUY) == (self.q.ratio > 0)
        for leg in self.others:
            found = ctx.instrument(leg.instrument_id)
            if found is None or not found.tradable:
                return ctx.fail(f"{leg.instrument_id} can't be traded")
        ctx.watch(*(leg.instrument_id for leg in self.others))
        self._report(ctx)
        self._quote(ctx)

    def on_tick(self, ctx: AlgoContext, tick: Tick) -> None:
        self._quote(ctx)

    def on_resume(self, ctx: AlgoContext) -> None:
        self._quote(ctx)

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        if child.status.is_final:
            self.modifies.pop(child.client_order_id, None)
        self._hedge(ctx)  # on every update: a state change can report the fill first
        self._report(ctx)
        if not self._short(ctx) and self.first_unhedged_at is not None:
            self.legging_ms.append((ctx.now() - self.first_unhedged_at).total_seconds() * 1000)
            self.first_unhedged_at = None
            self._report(ctx)

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        self._hedge(ctx)  # a hedge that couldn't go (no price yet) is tried again

    def done(self, ctx: AlgoContext) -> bool:
        return ctx.filled(ctx.instrument_id, ctx.side) >= ctx.qty and not self._short(ctx)

    def keep_on_stop(self, ctx: AlgoContext, child: OrderView) -> bool:
        return child.request.instrument_id != self.q.instrument_id  # hedges reduce risk

    def on_finish(self, ctx: AlgoContext) -> str | None:
        short = self._short(ctx)
        if not short:
            return None
        legs = ", ".join(f"{need} on {leg}" for leg, need in short.items())
        return (f"UNHEDGED {self.spread.name}: {legs} (quoting leg filled "
                f"{ctx.filled(ctx.instrument_id, ctx.side)}): hedge it by hand")  # fmt: skip

    # --- inside ---

    def _side(self, leg: SpreadLeg) -> Side:
        """The leg's side: buying the spread buys the + legs, sells the − legs."""
        return Side.BUY if (leg.ratio > 0) == self.buying else Side.SELL

    def _best(self, ctx: AlgoContext, leg: SpreadLeg) -> DepthLevel | None:
        """The price this leg would hedge at now: bought at its ask, sold at its bid."""
        tick = ctx.latest(leg.instrument_id)
        if tick is None:
            return None
        return tick.best_ask if self._side(leg) is Side.BUY else tick.best_bid

    def _target(self, ctx: AlgoContext, leg: SpreadLeg) -> int:
        """What this hedge leg must have filled for what the quoting leg has filled."""
        q_lots = ctx.filled(ctx.instrument_id, ctx.side) // ctx.instrument().lot_size
        lots = q_lots * abs(leg.ratio) // abs(self.q.ratio)
        return lots * ctx.instrument(leg.instrument_id).lot_size

    def _short(self, ctx: AlgoContext) -> dict[str, int]:
        """Each hedge leg still short of what the quoting leg's fills need (empty: hedged)."""
        out = {}
        for leg in self.others:
            need = self._target(ctx, leg) - ctx.filled(leg.instrument_id, self._side(leg))
            if need > 0:
                out[leg.instrument_id] = need
        return out

    def _quote_price(self, ctx: AlgoContext) -> int | None:
        legs = (self.q, *self.others)
        ages = [a for a in (ctx.age_s(x.instrument_id) for x in legs) if a is not None]
        if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:
            self.stale = f"leg prices {max(ages) - min(ages):.0f} s apart"  # #267
            return None
        self.stale = ""
        rest = 0
        for leg in self.others:
            level = self._best(ctx, leg)
            if level is None:
                return None
            rest += leg.multiplier * level.price_paise
        tick_size = ctx.instrument().tick_paise
        exact = Fraction(self.level - rest, self.q.multiplier * tick_size)  # in Q's ticks
        # buying the spread wants it at or below the level, selling at or above: Q's price
        # goes down for that when its multiplier helps (+ while buying), up otherwise
        down = self.buying == (self.q.multiplier > 0)
        price = (math.floor(exact) if down else math.ceil(exact)) * tick_size
        return price if price > 0 else None

    def _quote(self, ctx: AlgoContext) -> None:
        price = self._quote_price(ctx)
        quotes = [c for c in ctx.working() if c.request.instrument_id == ctx.instrument_id]
        if price is None:
            for c in quotes:
                ctx.cancel(c.client_order_id)  # no hedge price: no quote left out
            ctx.say(f"waiting: {self.stale}" if self.stale
                    else "waiting: a leg has no price to hedge against")  # fmt: skip
            return
        for c in quotes:
            if c.request.price_paise == price or c.pending is not Pending.NONE:
                continue
            done = self.modifies.get(c.client_order_id, 0)
            if done >= MODIFY_BUDGET:
                ctx.cancel(c.client_order_id)
                continue
            self.modifies[c.client_order_id] = done + 1
            ctx.modify(c.client_order_id, price_paise=price)
        if not quotes:
            want = ctx.remaining()
            if want > 0 and not self._short(ctx) and ctx.send(want, price):
                ctx.say(f"quoting {want} of {ctx.instrument_id} at {price / 100:.2f} "
                        f"(spread {self.level / 100:.2f})")  # fmt: skip

    def _hedge(self, ctx: AlgoContext) -> None:
        """Send each leg whatever the quoting leg's fills need that nothing covers yet."""
        short = self._short(ctx)
        if not short:
            return
        if self.first_unhedged_at is None:
            self.first_unhedged_at = ctx.now()
        self._sync(ctx)
        for leg in self.others:
            side = self._side(leg)
            covering = self.in_flight[leg.instrument_id] + sum(
                c.leaves_qty for c in ctx.working()
                if c.request.instrument_id == leg.instrument_id and c.request.side is side
            )  # fmt: skip
            need = short.get(leg.instrument_id, 0) - covering
            if need <= 0:
                continue
            level = self._best(ctx, leg)
            if level is None:
                ctx.say(f"UNHEDGED {need} on {leg.instrument_id}: no price yet")
                continue
            step = ctx.instrument(leg.instrument_id).tick_paise * leg.payup_ticks
            price = level.price_paise + (step if side is Side.BUY else -step)
            if ctx.send(need, price, side=side, instrument_id=leg.instrument_id,
                        check_remaining=False, hedge=True):  # fmt: skip
                self.in_flight[leg.instrument_id] += need
                ctx.say(f"hedging {need} of {leg.instrument_id} at {price / 100:.2f}")

    def _sync(self, ctx: AlgoContext) -> None:
        """A hedge the order manager has registered is no longer 'in flight'."""
        for c in ctx.children():
            leg = c.request.instrument_id
            if leg in self.in_flight and c.client_order_id not in self.seen:
                self.seen.add(c.client_order_id)
                self.in_flight[leg] = max(0, self.in_flight[leg] - c.request.qty)

    def _report(self, ctx: AlgoContext) -> None:
        """Figures for the algo screen, in words and numbers it shows as they are."""
        done = []
        traded = 0
        complete = True
        for leg in self.spread.legs:
            side = self._side(leg)
            filled = ctx.filled(leg.instrument_id, side)
            avg = ctx.average_price(leg.instrument_id, side)
            if avg is None:
                complete = False
            else:
                traded += leg.multiplier * avg
            verb = "bought" if side is Side.BUY else "sold"
            done.append(f"{_symbol(leg.instrument_id)} {verb} {filled}")
        short = self._short(ctx)
        ctx.report(
            spread_name=self.spread.name,
            spread_side="BUY" if self.buying else "SELL",
            level_paise=self.level,
            legs=" · ".join(done),
            unhedged=sum(short.values()),
            short=", ".join(f"{n} on {_symbol(i)}" for i, n in short.items()),
            traded_spread_paise=traded if complete else None,
            max_legging_ms=round(max(self.legging_ms)) if self.legging_ms else None,
        )


def _symbol(instrument_id: str) -> str:
    """ "NFO:NIFTY26NOVFUT" -> "NIFTY26NOVFUT" (the screens show symbols)."""
    return instrument_id.split(":", 1)[-1]
