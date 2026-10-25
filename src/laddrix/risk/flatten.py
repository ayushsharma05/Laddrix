"""STOP, cancel and flatten: three separate safe operations (#262).

    STOP     blocks every new order and change, cancels the working orders, stops the algos.
             It does NOT close positions. Its report says exactly what happened: what was
             asked to cancel, what couldn't be, what is still unconfirmed, what stays open.
    cancel   one order (the order book); always allowed, STOP or not.
    FLATTEN  closes open positions on purpose, and works while trading is stopped, so nobody
             has to resume trading just to reduce risk. Only through the exit desk
             (risk/closing.py): every order is reduce-only and marked a forced close, the only
             kind a kill switch lets through, and it still passes every other risk check.

Flatten refuses a position whose size isn't known: the broker and Laddrix disagree on it
(a reconcile alert, or a difference being re-checked). Closing a guessed size could open a
new position; the screen says to close it at the broker. Repeated clicks are safe: one
flatten runs at a time per account, and a second finds only what is still open.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from laddrix.domain.orders import Product
from laddrix.orders.model import Pending
from laddrix.positions.book import PositionBook
from laddrix.recovery import IST
from laddrix.risk.closing import ExitDesk

EXPLAIN = (
    "STOP blocks every new order and change, cancels working orders and stops algos. "
    "It does not close positions.",
    "Flatten closes open positions with orders that can only reduce them. It works while "
    "trading is stopped; nothing else is let through.",
)


class Flatten:
    def __init__(
        self,
        account_id: str,
        book: PositionBook,
        orders,  # OrderManager
        desk: ExitDesk,
        instruments,  # Callable[[str], Instrument | None]
        *,
        max_lots: int,
        unknown: Callable[[str, Product], str | None] = lambda i, p: None,
        stopped: Callable[[], Any] = lambda: None,  # the KillState that applies, or None
        algos: Callable[[], Any] = lambda: None,  # the AlgoEngine
        audit: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self._book = book
        self._orders = orders
        self._desk = desk
        self._instrument = instruments
        self.max_lots = max_lots  # closing pieces; an admin's edit replaces it
        self._unknown = unknown
        self._stopped = stopped
        self._algos = algos
        self._audit = audit
        self._clock = clock
        self._lock = asyncio.Lock()
        self.last: dict[str, Any] | None = None  # the last flatten, as the screens show it
        self.last_stop: dict[str, Any] | None = None  # the last STOP's report

    # --- what the screens show ----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        state = self._stopped()
        rows = []
        for p in self._book.positions():
            if not p.qty:
                continue
            closing = sum(
                v.leaves_qty
                for v in self._orders.working()
                if v.request.instrument_id == p.instrument_id and v.request.product is p.product
                and v.request.reduce_only
            )  # fmt: skip
            rows.append({"instrument_id": p.instrument_id, "product": p.product.value,
                         "qty": p.qty, "closing_qty": closing,
                         "unknown": self._unknown(p.instrument_id, p.product)})  # fmt: skip
        return {
            "account_id": self.account_id,
            "stopped": state is not None,
            "stop": None
            if state is None
            else {
                "reason": state.reason,
                "by": state.by,
                "at": state.at.isoformat() if state.at else None,
            },  # fmt: skip
            "stop_report": self.last_stop if state is not None else None,
            "positions": rows,
            "can_flatten": any(r["unknown"] is None for r in rows),
            "running": self._lock.locked(),
            "explain": list(EXPLAIN),
            "last": self.last,
        }

    def stop_report(self, before: list, failures: list[tuple[str, str]], algos_live: int,
                    who: str, reason: str) -> dict[str, Any]:  # fmt: skip
        """What a STOP did, in words a person can act on. `before`: the working orders when
        it was pressed; `failures`: the cancels that were refused."""
        failed = dict(failures)
        unconfirmed = [v.client_order_id for v in before if v.pending is Pending.UNCONFIRMED]
        left = set(failed) | set(unconfirmed)
        asked = [v.client_order_id for v in before if v.client_order_id not in left]
        open_ = [f"{p.instrument_id.split(':', 1)[-1]} {p.product.value} {p.qty:+d}"
                 for p in self._book.positions() if p.qty]  # fmt: skip
        lines = [f"stopped by {who}: {reason}"]
        lines.append(f"{len(asked)} working order{'s' * (len(asked) != 1)} asked to cancel"
                     + (f" ({', '.join(asked)})" if asked else ""))  # fmt: skip
        for cid, why in failed.items():
            lines.append(f"couldn't cancel {cid}: {why}; check it at the broker")
        if unconfirmed:
            what = ", ".join(unconfirmed)
            lines.append(f"{what}: no answer from the broker yet; cancelled the moment the "
                         "broker names it, else settled by its order list")  # fmt: skip
        lines.append(f"{algos_live} algo{'s' * (algos_live != 1)} stopped")
        lines.append(f"positions kept: {', '.join(open_)}; use Flatten to close them"
                     if open_ else "no open positions")  # fmt: skip
        report = {"at": self._clock().isoformat(), "by": who, "reason": reason,
                  "asked": asked, "failed": [{"client_order_id": c, "reason": r}
                                             for c, r in failed.items()],
                  "unconfirmed": unconfirmed, "algos_stopped": algos_live,
                  "positions_open": open_, "lines": lines}  # fmt: skip
        self.last_stop = report
        return report

    # --- flattening -----------------------------------------------------------------------

    async def run(self, user_id: str, *, instrument_id: str | None = None,
                  product: Product | None = None) -> dict[str, Any]:  # fmt: skip
        """Close the open positions (all, or one instrument, or one instrument and product).
        One at a time per account: a second click waits, then finds what is still open."""
        async with self._lock:
            basket = f"FL{self._clock().astimezone(IST).strftime('%H%M%S')}"
            why = f"flatten by {user_id}"
            rows = []
            for p in list(self._book.positions()):
                if not p.qty or (instrument_id and p.instrument_id != instrument_id):
                    continue
                if product is not None and p.product is not product:
                    continue
                row: dict[str, Any] = {"instrument_id": p.instrument_id,
                                       "product": p.product.value, "qty": p.qty}  # fmt: skip
                unknown = self._unknown(p.instrument_id, p.product)
                instrument = self._instrument(p.instrument_id)
                if unknown is not None or instrument is None:
                    why_not = unknown or "unknown instrument"
                    row.update(orders=[], refused=[], left_open=abs(p.qty), closed=False,
                               summary=f"not closed: {p.instrument_id} {p.product.value} "
                                       f"{p.qty:+d}: the size isn't known ({why_not}); "
                                       "close it at the broker")  # fmt: skip
                else:
                    done = await self._desk.close(instrument, p.product, user_id=user_id,
                                                  max_lots=self.max_lots, basket_id=basket,
                                                  why=why)  # fmt: skip
                    row.update(orders=done.ids, refused=done.refused,
                               left_open=done.left_open, closed=bool(done.ids),
                               summary=done.words())  # fmt: skip
                rows.append(row)
            result = {"at": self._clock().isoformat(), "by": user_id,
                      "stopped": self._stopped() is not None, "positions": rows,
                      "summary": "nothing open to close" if not rows else
                      "; ".join(r["summary"] for r in rows)}  # fmt: skip
            self.last = result
            if self._audit is not None:
                ids = [o for r in rows for o in r["orders"]]
                self._audit("FLATTEN", f"{why}: {result['summary']}",
                            {"user_id": user_id, "orders": ids})  # fmt: skip
            return result
