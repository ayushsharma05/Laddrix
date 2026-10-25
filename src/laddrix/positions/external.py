"""Trades made outside Laddrix (#355, part of #276): resolving a position mismatch they cause.

Someone trades in the broker's own app (Kite web, the phone, the broker's desk). The broker's
position moves; ours doesn't. Reconciling says they differ (positions/reconcile.py) and the
account can't be trusted until a person resolves it. This is that person's tool:

    preview(instrument, product) ─► the broker's position and ours, and the broker's trades
                                    in it that Laddrix didn't place (no order of ours)
        they explain the difference exactly ─► "these N trades explain it"
        they don't                          ─► why not, in words; nothing can be adopted
    adopt(..., by, acknowledgement)  (risk or admin, with words saying they checked)
        ─► the preview again, fresh; refused unless it explains the difference exactly
        ─► each trade into our positions at its own price (the cost basis stays true),
           noted with who, why, and the broker's trade id (EXTERNAL_ADOPTED)
        ─► a trade id already adopted is never adopted again
        ─► reconcile again: the mismatch clears

Never silently, never a guess: positions are never just set to the broker's number. A
difference the outside trades don't explain (a missed fill of ours, yesterday's trades)
stays an alert for a person to resolve at the broker. After a restart, recovery applies the
adopted trades again from the audit trail (recovery.py).
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from laddrix.broker.base import BrokerWithTrades
from laddrix.domain.orders import Product
from laddrix.domain.portfolio import Trade
from laddrix.positions.book import PositionBook

ADOPTED = "EXTERNAL_ADOPTED"


class ExternalError(ValueError):
    """Can't adopt: the message says why, in words."""


@dataclass(frozen=True)
class Proposal:
    instrument_id: str
    product: Product
    ours: int
    broker: int
    trades: tuple[Trade, ...]  # outside trades not yet adopted
    explains: bool
    words: str


def outside_trades(trades: Iterable[Trade], instrument_id: str, product: Product,
                   adopted: set[str]) -> list[Trade]:  # fmt: skip
    """The broker's trades in this instrument and product that no order of ours placed, each
    trade id once, minus those already adopted."""
    seen: dict[str, Trade] = {}
    for t in trades:
        if (t.instrument_id, t.product) == (instrument_id, product) and not t.client_order_id:
            seen[t.trade_id] = t
    return [t for tid, t in seen.items() if tid not in adopted]


def propose(instrument_id: str, product: Product, ours: int, broker: int,
            trades: list[Trade]) -> Proposal:  # fmt: skip
    gap = broker - ours
    outside = sum(t.side.sign * t.qty for t in trades)
    what = f"{instrument_id} {product.value}"
    if gap == 0:
        agree = f"{what}: Laddrix and the broker agree ({ours:+d}): nothing to adopt"
        return Proposal(instrument_id, product, ours, broker, (), False, agree)
    if trades and outside == gap:
        listed = ", ".join(f"{t.side.value} {t.qty} at ₹{t.price_paise / 100:,.2f} ({t.trade_id})"
                           for t in trades)  # fmt: skip
        return Proposal(instrument_id, product, ours, broker, tuple(trades), True,
                        f"{what}: {len(trades)} trade(s) made outside Laddrix explain the "
                        f"difference of {gap:+d}: {listed}")  # fmt: skip
    return Proposal(instrument_id, product, ours, broker, tuple(trades), False,
                    f"{what}: Laddrix shows {ours:+d}, the broker {broker:+d}, a difference of "
                    f"{gap:+d}; the broker's trades made outside Laddrix come to {outside:+d}, "
                    "so they don't explain it. Nothing is adopted: check the order history "
                    "and resolve it at the broker")  # fmt: skip


class ExternalTrades:
    """One account's tool for trades made outside Laddrix (see the module notes)."""

    def __init__(
        self,
        account_id: str,
        book: PositionBook,
        broker: BrokerWithTrades,
        *,
        note: Callable[[str, str, dict], None],  # the audit trail
        adopted_ids: Callable[[], set[str]],  # trade ids already adopted (the audit trail)
        recheck: Callable[[], Awaitable[object]] | None = None,  # reconcile again
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self._book = book
        self._broker = broker
        self._note = note
        self._adopted_ids = adopted_ids
        self._recheck = recheck
        self._clock = clock
        self._mine: set[str] = set()  # adopted here, before the audit trail has them on disk

    async def preview(self, instrument_id: str, product: Product) -> Proposal:
        positions = await self._broker.positions()
        broker = sum(p.qty for p in positions
                     if p.instrument_id == instrument_id and p.product is product)  # fmt: skip
        trades = outside_trades(await self._broker.trades(), instrument_id, product,
                                self._adopted_ids() | self._mine)  # fmt: skip
        return propose(instrument_id, product, self._book.qty(instrument_id, product), broker,
                       trades)  # fmt: skip

    async def adopt(self, instrument_id: str, product: Product, by: str,
                    acknowledgement: str) -> Proposal:  # fmt: skip
        if len(acknowledgement.strip()) < 10:
            raise ExternalError("say what you checked (at least 10 characters): it goes in "
                                "the audit trail with each adopted trade")  # fmt: skip
        proposal = await self.preview(instrument_id, product)  # fresh, never a stale preview
        if not proposal.explains:
            raise ExternalError(proposal.words)
        for t in proposal.trades:
            self._book.apply_fill(t.instrument_id, t.product, t.side, t.qty, t.price_paise)
            self._mine.add(t.trade_id)
            words = (f"{t.side.value} {t.qty} {t.instrument_id} {t.product.value} at "
                     f"₹{t.price_paise / 100:,.2f}, made outside Laddrix (broker trade "
                     f"{t.trade_id}), adopted by {by}: {acknowledgement.strip()}")  # fmt: skip
            self._note(ADOPTED, words, {
                "trade_id": t.trade_id, "broker_order_id": t.broker_order_id,
                "instrument_id": t.instrument_id, "product": t.product.value,
                "side": t.side.value, "qty": t.qty, "price_paise": t.price_paise, "by": by,
                "acknowledgement": acknowledgement.strip(),
            })  # fmt: skip
        if self._recheck is not None:
            await self._recheck()
        return proposal
