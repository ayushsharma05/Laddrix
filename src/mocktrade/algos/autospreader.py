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
                           at its best price ± its pay-up ticks (a limit)
    pieces (#422)       every order fits the account's per-order limits (lots and value):
                           a bigger hedge goes out in pieces at once; the quote rests one
                           piece (whole spread lots) at a time, the next once all is hedged
    every leg complete  ─► DONE; the spread actually traded (Σ price× · average) is reported

Several quoting legs (#427): every quoting leg rests a quote at the price that makes the spread
= P against all the other legs; a fill on any of them commits spread lots, and every other
leg (quoting ones too) is hedged for them. All quotes together never carry more spread lots
than are still to do (filled + working + on their way ≤ the order), so two quotes filling at
the same moment can't overfill: the lots left are shared out among the quoting legs (2 lots
over 2 legs: 1 + 1; 1 lot: one leg quotes). Each order says what it is (`algo_role`: quote,
hedge, flatten), as a leg can carry a quote and a hedge on the same side.

The spread's own controls (#425):

    re-quote threshold   the quote moves only when its target has moved this many ticks;
                         a move that would make the spread worse than P is made at once
    max unhedged         a quote never carries more than this many spread lots
    chase                a working hedge follows the market to its new best ± pay-up, never
                         more than this many ticks past its first price
    hedge timeout        a leg unhedged this long: an ALERT once, and the status keeps
                         saying it ("UNHEDGED 75 on NOV for 6 s") until it is hedged

The trader's actions on a running ASE, each previewed in words and done only as previewed:

    Hedge now   every short leg crosses the market within the broker's protection: working
                hedges are moved there, what nothing covers is sent there
    Flatten     undo what is unhedged: stop quoting, cancel every working child, then close
                each leg's part beyond the complete spread lots, crossing the market; the
                algo ends with only whole spreads (or nothing) left

Buying the spread buys the + legs and sells the − legs; selling it mirrors that.

After a restart (#426) it comes back PAUSED with its children, from the spread copied when it
started (kept in the audit trail): working hedges keep working, its quote is cancelled, and
nothing new is sent (no hedge either) until the trader resumes it, after checking what
happened while the server was down. "Hedge now" and "Flatten" work at once.

Rules (the two-leg spreader's, for N legs):
- Pause and stop pull the quote but keep hedges working: they reduce risk. A hedge sent
  while paused or stopping stays working too.
- A fill that crosses a cancel is still hedged. Hedging is idempotent: it only sends what no
  fill or working hedge covers yet, so a repeated or out-of-order update never over-hedges.
- No new quote while any leg is unhedged, while a leg has no price, or while the legs'
  prices are more than MAX_LEG_SKEW_S apart (one is stale, #267).
- It fails at start, in words, if any leg can't fit what it must trade in one order.
- The algo ends only with every leg complete. Ending with any leg short (stopped, a kill
  switch, a hedge refused) raises an alert naming each leg and quantity.
"""

import math
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from mocktrade.algos.engine import ActionPreview, AlgoAction, AlgoContext, AlgoType, BaseStrategy
from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec
from mocktrade.broker.rules import protected_limit
from mocktrade.domain.orders import Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.orders.model import OrderEvent, OrderView, Pending
from mocktrade.spreads.model import (
    MAX_LEG_SKEW_S,
    SpreadDef,
    SpreadLeg,
    spread_from_dict,
    spread_to_dict,
)

MODIFY_BUDGET = 24  # brokers allow 25 modifications per order
AUTO = Decimal(-1)  # the broker's automatic market protection
CHANGE_WAIT_S = 1.0  # how long a change asked waits for its answer before another may go

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

HEDGE_NOW = AlgoAction("hedge_now", "Hedge now",
                       "Every unhedged leg crosses the market now, within the broker's "
                       "protection")  # fmt: skip
FLATTEN = AlgoAction("flatten", "Flatten",
                     "Undo what is unhedged: stop, cancel every working order, and close each "
                     "leg's part beyond the complete spreads")  # fmt: skip


def algo_type(lookup: Callable[[str], SpreadDef | None]) -> AlgoType:
    """The ASE algo, finding spreads with `lookup` (the server's SpreadBook.get); after a
    restart, rebuilt from the spread it copied when it started."""
    return AlgoType(SPEC, lambda params: AutoSpreader(params, lookup), restore=_restore)


def _restore(params: dict, snapshot: dict | None) -> "AutoSpreader":
    if not snapshot or "spread" not in snapshot:
        raise ValueError("the spread it was trading was not saved")
    spread = spread_from_dict(snapshot["spread"])
    return AutoSpreader(params, lambda _id: spread)


def _other_side(side: Side) -> Side:
    return Side.SELL if side is Side.BUY else Side.BUY


class AutoSpreader(BaseStrategy):
    actions = (HEDGE_NOW, FLATTEN)

    def __init__(self, params: dict, lookup: Callable[[str], SpreadDef | None]) -> None:
        spread = lookup(params["spread"])
        if spread is None:
            raise ValueError(f"there is no spread {params['spread']}")
        self.spread = spread  # a copy: the definition can change, this algo doesn't
        self.controls = spread.controls
        self.level: int = params["level_paise"]
        self.q: SpreadLeg = spread.quote_leg  # the parent order's leg (the first quoting one)
        self.quoting: tuple[SpreadLeg, ...] = spread.quote_legs
        self.legs: tuple[SpreadLeg, ...] = spread.legs
        self.others: tuple[SpreadLeg, ...] = tuple(x for x in spread.legs if x is not self.q)
        self.buying = True  # buying the spread (set at start, from the parent's side)
        self.modifies: dict[str, int] = {}
        # orders asked to change whose answer hasn't come yet: ctx.modify only sends later, so
        # two ticks in a row would both see nothing pending and ask twice. A change refused
        # quietly brings no answer, so the mark lapses after CHANGE_WAIT_S.
        self.changing: dict[str, datetime] = {}
        self.in_flight: dict[str, int] = {leg.instrument_id: 0 for leg in spread.legs}  # hedges
        self.quote_in_flight: dict[str, int] = {leg.instrument_id: 0 for leg in spread.legs}
        self.seen: set[str] = set()  # children already counted out of `in_flight`
        self.hedge_first: dict[str, int] = {}  # each hedge's first price (the chase's anchor)
        self.first_unhedged_at: datetime | None = None
        self.alerted = False  # the hedge timeout's ALERT, once per unhedged spell
        self.legging_ms: list[float] = []
        self.stale = ""
        self.flattening = False
        self.restored = False  # rebuilt after a restart: nothing new is sent until resumed

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
        if why := self._too_big(ctx):
            return ctx.fail(why)
        ctx.watch(*(leg.instrument_id for leg in self.others))
        self._report(ctx)
        self._quote(ctx)

    def on_tick(self, ctx: AlgoContext, tick: Tick) -> None:
        self._quote(ctx)
        self._chase(ctx)

    def on_resume(self, ctx: AlgoContext) -> None:
        self.restored = False  # the trader has looked: hedge what is short, then quote
        self._hedge(ctx)
        self._quote(ctx)

    def snapshot(self) -> dict:
        return {"spread": spread_to_dict(self.spread)}

    def on_restore(self, ctx: AlgoContext) -> None:
        """Back after a restart, paused: none of its children is in flight (each is counted,
        and a hedge's price kept as its chase anchor, the first time `_sync` sees it), and a
        leg short now counts as unhedged from now."""
        self.restored = True
        self.buying = (ctx.side is Side.BUY) == (self.q.ratio > 0)
        ctx.watch(*(leg.instrument_id for leg in self.others))
        if self._short(ctx):
            self.first_unhedged_at = ctx.now()
        self._report(ctx)

    def on_child(self, ctx: AlgoContext, child: OrderView, event: OrderEvent) -> None:
        self.changing.pop(child.client_order_id, None)  # the broker answered (or it ended)
        if child.status.is_final:
            self.modifies.pop(child.client_order_id, None)
        if self.flattening:
            self._flatten(ctx)
            self._report(ctx)
            return
        self._hedge(ctx)  # on every update: a state change can report the fill first
        self._report(ctx)
        if not self._short(ctx) and self.first_unhedged_at is not None:
            self.legging_ms.append((ctx.now() - self.first_unhedged_at).total_seconds() * 1000)
            self.first_unhedged_at = None
            self.alerted = False
            self._report(ctx)

    def on_timer(self, ctx: AlgoContext, now: datetime) -> None:
        self.on_watch(ctx, now)

    def on_watch(self, ctx: AlgoContext, now: datetime) -> None:
        """Also while paused or stopping: hedges, the chase, the timeout, a flatten."""
        if self.flattening:
            self._flatten(ctx)
            return
        self._hedge(ctx)  # a hedge that couldn't go (no price yet) is tried again
        self._chase(ctx)
        self._timeout(ctx)

    def done(self, ctx: AlgoContext) -> bool:
        return self._lots_done(ctx) >= self._lots(ctx) and not self._short(ctx)

    def keep_on_stop(self, ctx: AlgoContext, child: OrderView) -> bool:
        # only quotes are pulled: hedges (and a flatten's closing orders) reduce risk
        return not self._is_quote(child)

    def on_finish(self, ctx: AlgoContext) -> str | None:
        short = self._short(ctx)
        if not short:
            return None
        legs = ", ".join(f"{need} on {leg}" for leg, need in short.items())
        return (f"UNHEDGED {self.spread.name}: {legs} (quoting leg filled "
                f"{self._quoted(ctx, self.q)}): hedge it by hand")  # fmt: skip

    # --- the trader's actions (#425) ---

    def preview(self, ctx: AlgoContext, name: str) -> ActionPreview:
        if name == HEDGE_NOW.name:
            return self._hedge_now(ctx, act=False)
        return self._flatten_preview(ctx)

    def act(self, ctx: AlgoContext, name: str) -> None:
        if name == HEDGE_NOW.name:
            self._hedge_now(ctx, act=True)
            return
        self.flattening = True
        for c in ctx.working():
            ctx.cancel(c.client_order_id)
        ctx.say("flattening: cancelling working orders, then undoing what is unhedged")
        self._flatten(ctx)

    # --- inside ---

    def _side(self, leg: SpreadLeg) -> Side:
        """The leg's side: buying the spread buys the + legs, sells the − legs."""
        return Side.BUY if (leg.ratio > 0) == self.buying else Side.SELL

    def _net(self, ctx: AlgoContext, leg: SpreadLeg) -> int:
        """What this leg has filled on its own side, less what a flatten closed."""
        side = self._side(leg)
        return ctx.filled(leg.instrument_id, side) - ctx.filled(
            leg.instrument_id, _other_side(side)
        )

    def _best(
        self, ctx: AlgoContext, leg: SpreadLeg, side: Side | None = None
    ) -> DepthLevel | None:
        """The price this leg would trade at now: bought at its ask, sold at its bid."""
        tick = ctx.latest(leg.instrument_id)
        if tick is None:
            return None
        return tick.best_ask if (side or self._side(leg)) is Side.BUY else tick.best_bid

    def _crossing(self, ctx: AlgoContext, leg: SpreadLeg, side: Side) -> int | None:
        """A price that crosses the market now, within the broker's protection band."""
        level = self._best(ctx, leg, side)
        if level is None:
            return None
        return protected_limit(ctx.instrument(leg.instrument_id), side, level.price_paise, AUTO)

    def _is_quote(self, c: OrderView) -> bool:
        """One of our quotes (orders kept from before #427 carry no role: then a quote is
        the parent leg's own side)."""
        r = c.request
        if r.algo_role:
            return r.algo_role == "quote"
        return r.instrument_id == self.q.instrument_id and r.side is self._side(self.q)

    def _quotes_on(self, ctx: AlgoContext, leg: SpreadLeg) -> list[OrderView]:
        return [c for c in ctx.working()
                if c.request.instrument_id == leg.instrument_id and self._is_quote(c)]  # fmt: skip

    def _lot(self, ctx: AlgoContext, leg: SpreadLeg) -> int:
        return ctx.instrument(leg.instrument_id).lot_size

    def _quoted(self, ctx: AlgoContext, leg: SpreadLeg) -> int:
        """What this leg's quotes have filled, less what a flatten closed on it."""
        mine = (c for c in ctx.children() if c.request.instrument_id == leg.instrument_id)
        filled = sum(c.filled_qty for c in mine if self._is_quote(c))
        return filled - ctx.filled(leg.instrument_id, _other_side(self._side(leg)))

    def _lots(self, ctx: AlgoContext) -> int:
        """The spread lots asked (the parent's quantity is on the first quoting leg)."""
        return ctx.qty // (abs(self.q.ratio) * self._lot(ctx, self.q))

    def _lots_done(self, ctx: AlgoContext) -> Fraction:
        """Spread lots the quotes' fills have committed (a part of one: a fraction)."""
        return sum((Fraction(self._quoted(ctx, x), abs(x.ratio) * self._lot(ctx, x))
                    for x in self.quoting), Fraction(0))  # fmt: skip

    def _target(self, ctx: AlgoContext, leg: SpreadLeg) -> int:
        """What this leg must have filled for what the quotes have filled: its own quote fills,
        and for each other quoting leg's fills its share in whole lots."""
        lot = self._lot(ctx, leg)
        target = 0
        for q in self.quoting:
            if q is leg:
                target += self._quoted(ctx, q)
            else:
                q_lots = self._quoted(ctx, q) // self._lot(ctx, q)
                target += q_lots * abs(leg.ratio) // abs(q.ratio) * lot
        return target

    def _short(self, ctx: AlgoContext) -> dict[str, int]:
        """Each leg still short of what the quotes' fills need (empty: hedged)."""
        out = {}
        for leg in self.legs:
            need = self._target(ctx, leg) - self._net(ctx, leg)
            if need > 0:
                out[leg.instrument_id] = need
        return out

    def _quote_price(self, ctx: AlgoContext, quote: SpreadLeg | None = None) -> int | None:
        """Where this quoting leg's quote makes the spread = P against every other leg's
        price now (the price it would be hedged at)."""
        quote = quote or self.q
        ages = [a for a in (ctx.age_s(x.instrument_id) for x in self.legs) if a is not None]
        if ages and max(ages) - min(ages) > MAX_LEG_SKEW_S:
            self.stale = f"leg prices {max(ages) - min(ages):.0f} s apart"  # #267
            return None
        self.stale = ""
        rest = 0
        for leg in self.legs:
            if leg is quote:
                continue
            level = self._best(ctx, leg)
            if level is None:
                return None
            rest += leg.multiplier * level.price_paise
        tick_size = ctx.instrument(quote.instrument_id).tick_paise
        exact = Fraction(self.level - rest, quote.multiplier * tick_size)  # in its ticks
        price = (math.floor(exact) if self._down(quote) else math.ceil(exact)) * tick_size
        return price if price > 0 else None

    def _down(self, quote: SpreadLeg | None = None) -> bool:
        """Buying the spread wants it at or below the level, selling at or above: a quote's
        price rounds down for that when its multiplier helps (+ while buying), up otherwise.
        A resting quote above (down) or below (up) its target would trade the spread worse."""
        return self.buying == ((quote or self.q).multiplier > 0)

    def _changing(self, ctx: AlgoContext, client_order_id: str) -> bool:
        """A change to this order was asked a moment ago and not yet answered."""
        asked = self.changing.get(client_order_id)
        return asked is not None and (ctx.now() - asked).total_seconds() < CHANGE_WAIT_S

    def _cap(self, ctx: AlgoContext, instrument_id: str, price: int | None) -> int | None:
        """The most one order on this leg may carry at this price, or None: no limit known.
        Asked each time: the account's limits can change while the algo works."""
        return ctx.max_order_qty(instrument_id, price)

    def _piece(
        self, ctx: AlgoContext, price: int | None, quote: SpreadLeg | None = None
    ) -> int | None:
        """The biggest quote allowed on this quoting leg: it fits one order and the spread's
        max unhedged, in whole spread lots (0: not even one; None: no limit)."""
        quote = quote or self.q
        unit = abs(quote.ratio) * self._lot(ctx, quote)
        caps = [self._cap(ctx, quote.instrument_id, price)]
        if self.controls.max_unhedged_lots:
            caps.append(self.controls.max_unhedged_lots * unit)
        known = [c for c in caps if c is not None]
        if not known:
            return None
        return min(known) // unit * unit

    def _too_big(self, ctx: AlgoContext) -> str | None:
        """Why a leg can't trade even its smallest piece in one order at its price now."""
        for q in self.quoting:
            if self._piece(ctx, self._quote_price(ctx, q), q) == 0:
                return (f"one spread lot ({abs(q.ratio)} lots of {q.instrument_id}) "
                        f"is over this account's per-order limit")  # fmt: skip
        for leg in self.others:
            level = self._best(ctx, leg)
            if self._cap(ctx, leg.instrument_id, level.price_paise if level else None) == 0:
                return f"one lot of {leg.instrument_id} is over this account's per-order limit"
        return None

    def _quote(self, ctx: AlgoContext) -> None:
        if self.flattening:
            return
        self._sync(ctx)  # quotes the order manager has registered are no longer on their way
        idle: list[tuple[SpreadLeg, int, int | None]] = []  # quoting legs with no quote
        waiting = ""
        for q in self.quoting:
            price = self._quote_price(ctx, q)
            quotes = self._quotes_on(ctx, q)
            if price is None:
                for c in quotes:
                    ctx.cancel(c.client_order_id)  # no hedge price: no quote left out
                waiting = (f"waiting: {self.stale}" if self.stale
                           else "waiting: a leg has no price to hedge against")  # fmt: skip
                continue
            piece = self._piece(ctx, price, q)
            self._requote(ctx, q, quotes, price, piece)
            if not quotes:  # one on its way counts in what is committed: no second one
                idle.append((q, price, piece))
        if waiting:
            ctx.say(waiting)
        if idle and not self._short(ctx):
            self._new_quotes(ctx, idle)

    def _requote(self, ctx: AlgoContext, q: SpreadLeg, quotes: list[OrderView], price: int,
                 piece: int | None) -> None:  # fmt: skip
        tick = ctx.instrument(q.instrument_id).tick_paise
        for c in quotes:
            current = c.request.price_paise
            if current == price or current is None or c.pending is not Pending.NONE:
                continue
            if self._changing(ctx, c.client_order_id):
                continue
            if piece is not None and c.leaves_qty > piece:
                ctx.cancel(c.client_order_id)  # too big at the new price: re-quote smaller
                continue
            worse = current > price if self._down(q) else current < price
            if not worse and abs(price - current) < self.controls.requote_ticks * tick:
                continue  # a better chance, but under the threshold: keep the modifications
            done = self.modifies.get(c.client_order_id, 0)
            if done >= MODIFY_BUDGET:
                ctx.cancel(c.client_order_id)
                continue
            self.modifies[c.client_order_id] = done + 1
            self.changing[c.client_order_id] = ctx.now()
            ctx.modify(c.client_order_id, price_paise=price)

    def _committed(self, ctx: AlgoContext) -> Fraction:
        """Spread lots the quotes have taken: filled, working and on their way."""
        self._sync(ctx)
        total = Fraction(0)
        for q in self.quoting:
            working = sum(c.leaves_qty for c in self._quotes_on(ctx, q))
            qty = self._quoted(ctx, q) + working + self.quote_in_flight[q.instrument_id]
            total += Fraction(qty, abs(q.ratio) * self._lot(ctx, q))
        return total

    def _new_quotes(self, ctx: AlgoContext, idle: Iterable[tuple[SpreadLeg, int, int | None]]):
        """Share the spread lots left among the quoting legs with no quote: together the quotes
        never carry more than is still to do (#427)."""
        free = self._lots(ctx) - self._committed(ctx)
        idle = list(idle)
        for n, (q, price, piece) in enumerate(idle):
            if free <= 0:
                return
            lot = self._lot(ctx, q)
            share = free / (len(idle) - n)
            lots = math.floor(share * abs(q.ratio))
            if not lots and free * abs(q.ratio) >= 1:
                lots = 1  # less than a lot each: the first legs take what there is
            want = lots * lot if piece is None else min(lots * lot, piece)
            if piece == 0:
                ctx.say(f"waiting: one spread lot at {price / 100:.2f} is over the per-order limit")
            elif want > 0 and ctx.send(want, price, side=self._side(q),
                                       instrument_id=q.instrument_id, check_remaining=False,
                                       role="quote"):  # fmt: skip
                self.quote_in_flight[q.instrument_id] += want
                free -= Fraction(want, abs(q.ratio) * lot)
                ctx.say(f"quoting {want} of {q.instrument_id} at {price / 100:.2f} "
                        f"(spread {self.level / 100:.2f})")  # fmt: skip

    def _covering(self, ctx: AlgoContext, leg: SpreadLeg, side: Side) -> int:
        """What working orders and orders on their way will still add on this leg and side
        (not quotes: they may never fill)."""
        return self.in_flight[leg.instrument_id] + sum(
            c.leaves_qty for c in ctx.working()
            if c.request.instrument_id == leg.instrument_id and c.request.side is side
            and not self._is_quote(c)
        )  # fmt: skip

    def _hedge(self, ctx: AlgoContext) -> None:
        """Send each leg whatever the quoting leg's fills need that nothing covers yet."""
        if self.flattening or self.restored:  # restored: the trader decides first (#426)
            return
        short = self._short(ctx)
        if not short:
            return
        if self.first_unhedged_at is None:
            self.first_unhedged_at = ctx.now()
        self._sync(ctx)
        for leg in self.legs:
            side = self._side(leg)
            need = short.get(leg.instrument_id, 0) - self._covering(ctx, leg, side)
            if need <= 0:
                continue
            level = self._best(ctx, leg)
            if level is None:
                ctx.say(f"UNHEDGED {need} on {leg.instrument_id}: no price yet")
                continue
            step = ctx.instrument(leg.instrument_id).tick_paise * leg.payup_ticks
            price = level.price_paise + (step if side is Side.BUY else -step)
            self._send(ctx, leg, side, need, price, "hedging")

    def _send(self, ctx: AlgoContext, leg: SpreadLeg, side: Side, need: int, price: int,
              verb: str, role: str = "hedge") -> None:  # fmt: skip
        """Send `need` on a leg (never the quote), in pieces that each fit one order (#422),
        all at once."""
        cap = self._cap(ctx, leg.instrument_id, price)
        if cap == 0:
            ctx.say(f"UNHEDGED {need} on {leg.instrument_id}: one lot is over the "
                    f"per-order limit")  # fmt: skip
            return
        while need > 0:
            piece = need if cap is None else min(need, cap)
            if not ctx.send(piece, price, side=side, instrument_id=leg.instrument_id,
                            check_remaining=False, hedge=True, role=role):  # fmt: skip
                break
            self.in_flight[leg.instrument_id] += piece
            need -= piece
            ctx.say(f"{verb} {piece} of {leg.instrument_id} at {price / 100:.2f}")

    def _sync(self, ctx: AlgoContext) -> None:
        """An order the order manager has registered is no longer 'in flight'; a hedge's
        first price is kept (the chase's anchor)."""
        for c in ctx.children():
            leg = c.request.instrument_id
            if c.client_order_id in self.seen or leg not in self.in_flight:
                continue
            self.seen.add(c.client_order_id)
            if self._is_quote(c):
                self.quote_in_flight[leg] = max(0, self.quote_in_flight[leg] - c.request.qty)
                continue
            self.in_flight[leg] = max(0, self.in_flight[leg] - c.request.qty)
            if c.request.price_paise is not None:
                self.hedge_first[c.client_order_id] = c.request.price_paise

    def _chase(self, ctx: AlgoContext) -> None:
        """Working hedges follow the market to its best ± pay-up, never more than `chase`
        ticks past their first price; they never move back."""
        if self.flattening or self.restored or not self.controls.chase_ticks:
            return
        self._sync(ctx)
        for leg in self.legs:
            side = self._side(leg)
            level = self._best(ctx, leg)
            if level is None:
                continue
            tick = ctx.instrument(leg.instrument_id).tick_paise
            step = tick * leg.payup_ticks
            for c in ctx.working():
                r = c.request
                first = self.hedge_first.get(c.client_order_id)
                if r.instrument_id != leg.instrument_id or r.side is not side or first is None:
                    continue
                if c.pending is not Pending.NONE or r.price_paise is None:
                    continue
                if self._changing(ctx, c.client_order_id):
                    continue
                reach = tick * self.controls.chase_ticks
                if side is Side.BUY:
                    target = min(level.price_paise + step, first + reach)
                    moves = target > r.price_paise
                else:
                    target = max(level.price_paise - step, first - reach)
                    moves = target < r.price_paise
                done = self.modifies.get(c.client_order_id, 0)
                if moves and done < MODIFY_BUDGET:  # at the budget a hedge stays: never pulled
                    self.modifies[c.client_order_id] = done + 1
                    self.changing[c.client_order_id] = ctx.now()
                    ctx.modify(c.client_order_id, price_paise=target)
                    ctx.say(f"chasing {c.leaves_qty} of {leg.instrument_id} to {target / 100:.2f}")

    def _timeout(self, ctx: AlgoContext) -> None:
        """A leg unhedged longer than the spread's hedge timeout: say it, and ALERT once."""
        short = self._short(ctx)
        if not short or self.first_unhedged_at is None:
            return
        waited = (ctx.now() - self.first_unhedged_at).total_seconds()
        if waited < self.controls.hedge_timeout_s:
            return
        legs = ", ".join(f"{n} on {_symbol(i)}" for i, n in short.items())
        text = f"UNHEDGED {legs} for {int(waited)} s: hedge now or flatten"
        ctx.say(text)
        if not self.alerted:
            self.alerted = True
            ctx.alert(f"{self.spread.name}: {text}")

    def _hedge_now(self, ctx: AlgoContext, *, act: bool) -> ActionPreview:
        """Preview (or do) crossing the market on every short leg."""
        short = self._short(ctx)
        if not short:
            return ActionPreview(HEDGE_NOW.name, HEDGE_NOW.label, (), False, "nothing is unhedged")
        self._sync(ctx)
        lines: list[str] = []
        for leg in self.legs:
            need = short.get(leg.instrument_id, 0)
            if not need:
                continue
            side = self._side(leg)
            price = self._crossing(ctx, leg, side)
            if price is None:
                return ActionPreview(HEDGE_NOW.name, HEDGE_NOW.label, (), False,
                                     f"{_symbol(leg.instrument_id)} has no price")  # fmt: skip
            working = [
                c for c in ctx.working()
                if c.request.instrument_id == leg.instrument_id and c.request.side is side
                and not self._is_quote(c)
            ]  # fmt: skip
            for c in working:
                lines.append(f"move {side.value} {c.leaves_qty} {_symbol(leg.instrument_id)} "
                             "to cross the market")  # fmt: skip
                if act and c.pending is Pending.NONE:
                    ctx.modify(c.client_order_id, price_paise=price)
            rest = need - self._covering(ctx, leg, side)
            if rest > 0:
                lines.append(f"{side.value} {rest} {_symbol(leg.instrument_id)}, crossing the "
                             "market")  # fmt: skip
                if act:
                    self._send(ctx, leg, side, rest, price, "hedging now")
        return ActionPreview(HEDGE_NOW.name, HEDGE_NOW.label, tuple(lines))

    def _excess(self, ctx: AlgoContext) -> dict[str, int]:
        """Each leg's filled part beyond the complete spread lots (what a flatten closes)."""
        legs = self.spread.legs
        lots = {leg.instrument_id: ctx.instrument(leg.instrument_id).lot_size for leg in legs}
        complete = min(self._net(ctx, leg) // (abs(leg.ratio) * lots[leg.instrument_id])
                       for leg in legs)  # fmt: skip
        out = {}
        for leg in legs:
            extra = self._net(ctx, leg) - complete * abs(leg.ratio) * lots[leg.instrument_id]
            if extra > 0:
                out[leg.instrument_id] = extra
        return out

    def _flatten_preview(self, ctx: AlgoContext) -> ActionPreview:
        working = ctx.working()
        excess = self._excess(ctx)
        if not excess and not self._short(ctx):
            return ActionPreview(FLATTEN.name, FLATTEN.label, (), False,
                                 "nothing is unhedged: use Stop")  # fmt: skip
        lines = [f"cancel {c.request.side.value} {c.leaves_qty} "
                 f"{_symbol(c.request.instrument_id)}" for c in working]  # fmt: skip
        for leg in self.spread.legs:
            extra = excess.get(leg.instrument_id)
            if extra:
                side = _other_side(self._side(leg))
                lines.append(f"{side.value} {extra} {_symbol(leg.instrument_id)}, crossing the "
                             "market (if still unhedged once the cancels are done)")  # fmt: skip
        return ActionPreview(FLATTEN.name, FLATTEN.label, tuple(lines))

    def _flatten(self, ctx: AlgoContext) -> None:
        """Once nothing of ours is working but closing orders: close each leg's excess."""
        self._sync(ctx)
        working = ctx.working()
        if any(c.request.side is self._side(self._leg(c.request.instrument_id)) for c in working):
            return  # a cancel hasn't landed yet: the fill it may bring changes the excess
        if any(self.in_flight.values()) or ctx.sending:
            return
        excess = self._excess(ctx)
        if not excess and not working:
            done = " · ".join(f"{_symbol(x.instrument_id)} {self._net(ctx, x)}"
                              for x in self.spread.legs)  # fmt: skip
            ctx.finish(f"flattened: {done}")
            return
        for leg in self.spread.legs:
            side = _other_side(self._side(leg))
            need = excess.get(leg.instrument_id, 0) - self._covering(ctx, leg, side)
            if need <= 0:
                continue
            price = self._crossing(ctx, leg, side)
            if price is None:
                ctx.say(f"flattening: {_symbol(leg.instrument_id)} has no price yet")
                continue
            self._send(ctx, leg, side, need, price, "flattening", role="flatten")

    def _leg(self, instrument_id: str) -> SpreadLeg:
        return next(x for x in self.spread.legs if x.instrument_id == instrument_id)

    def _report(self, ctx: AlgoContext) -> None:
        """Figures for the algo screen, in words and numbers it shows as they are."""
        done = []
        traded = 0
        complete = True
        for leg in self.spread.legs:
            side = self._side(leg)
            filled = self._net(ctx, leg)
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
            lots=self._lots(ctx),
            lots_done=math.floor(self._lots_done(ctx)),
            legs=" · ".join(done),
            unhedged=sum(short.values()),
            short=", ".join(f"{n} on {_symbol(i)}" for i, n in short.items()),
            traded_spread_paise=traded if complete else None,
            max_legging_ms=round(max(self.legging_ms)) if self.legging_ms else None,
        )


def _symbol(instrument_id: str) -> str:
    """ "NFO:NIFTY26NOVFUT" -> "NIFTY26NOVFUT" (the screens show symbols)."""
    return instrument_id.split(":", 1)[-1]
