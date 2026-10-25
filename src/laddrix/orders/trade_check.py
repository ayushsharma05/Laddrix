"""Orders against the broker's own trade list (#352, part of #275).

The broker's tradebook is the authority on what was traded: one row per exchange trade,
each with its trade id and its own price (whole paise, on the tick grid). Our orders learn
of fills from order updates, which give only a filled quantity and an average. This checks
the two agree, each time the trade list is read:

    trades ─► ledger: per broker order, the sum of its trades (qty, value), each trade id
              once (a trade read twice, or again in a later list, counts once; a trade id
              that comes back with a new price or qty replaces the old one: a correction)
    orders ─► for each of our orders the ledger knows, or that has fills:
              same qty, different value ─► the trades are right: the order is corrected
                                           (a CORRECTED event, positions follow)
              different qty              ─► named, in the audit trail, once it has differed
                                           on two reads in a row (a trade list can lag the
                                           order updates by a moment); not changed by itself

Never silently: every correction is an event with what it was and what it became.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from laddrix.domain.portfolio import Trade
from laddrix.orders.model import OrderView

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeGap:
    """An order whose filled quantity differs from its trades at the broker."""

    client_order_id: str
    broker_order_id: str
    ours_qty: int
    broker_qty: int

    @property
    def words(self) -> str:
        return (f"order {self.client_order_id} ({self.broker_order_id}): Laddrix has "
                f"{self.ours_qty} filled, the broker's trades {self.broker_qty}")  # fmt: skip


def ledger(trades: Iterable[Trade]) -> dict[str, tuple[int, int]]:
    """Per broker order id: (qty, value in paise) of its trades, each trade id once (the
    last row for an id wins: a corrected trade replaces the first)."""
    by_id: dict[tuple[str, str], Trade] = {}
    for t in trades:
        by_id[(t.broker_order_id, t.trade_id)] = t
    totals: dict[str, tuple[int, int]] = {}
    for t in by_id.values():
        qty, value = totals.get(t.broker_order_id, (0, 0))
        totals[t.broker_order_id] = (qty + t.qty, value + t.qty * t.price_paise)
    return totals


class TradeCheck:
    """One account's orders against its trade list (see the module notes)."""

    def __init__(
        self,
        orders,  # OrderManager
        note: Callable[[str, str, dict], None] = lambda _k, _d, _x: None,  # the audit trail
    ) -> None:
        self._orders = orders
        self._note = note
        self._seen: dict[str, TradeGap] = {}  # differed on the last read
        self.gaps: list[TradeGap] = []  # differing now, on two reads in a row

    def check(self, trades: Iterable[Trade]) -> list[str]:
        """Compare; correct values, name quantities. Returns what it did, in words."""
        totals = ledger(trades)
        done: list[str] = []
        seen: dict[str, TradeGap] = {}
        for view in self._orders.orders():
            done += self._one(view, totals, seen)
        for key, gap in seen.items():  # named once, when it has lasted two reads
            if self._seen.get(key) == gap and gap not in self.gaps:
                self._note("TRADES_DIFFER", gap.words, {"client_order_id": gap.client_order_id,
                           "ours": gap.ours_qty, "broker": gap.broker_qty})  # fmt: skip
                done.append(gap.words)
        self.gaps = [g for k, g in seen.items() if self._seen.get(k) == g]
        self._seen = seen
        return done

    def _one(self, view: OrderView, totals, seen: dict[str, TradeGap]) -> list[str]:
        bid = view.broker_order_id
        if bid is None:
            return []  # (no fills and no trades: 0 and 0 agree below)
        qty, value = totals.get(bid, (0, 0))
        if qty != view.filled_qty:
            seen[view.client_order_id] = TradeGap(view.client_order_id, bid, view.filled_qty, qty)
            return []
        if value == view.filled_value_paise:
            return []
        words = self._orders.correct_value(view.client_order_id, value,
                                           "the broker's trade list")  # fmt: skip
        return [words] if words else []
