"""The square-off assistant: intraday (MIS) positions before the broker closes them itself.

Brokers square off MIS positions shortly before the close, at their own price and for a fee.
From `warn_from` the assistant lists the open MIS positions with the time left (an alert on
every screen, refreshed each minute); one confirmed click closes them all through the normal
order path (risk/closing.py), audited as SQUARE_OFF with who asked.

The cut-off times are data (`CUTOFFS`, by exchange), published by the server (GET
…/square-off), never written into the screens. Checked 15 Oct 2026 against the broker's
published times: equity 15:20, F&O 15:25 IST. Re-check before trading real money: brokers
change them (e.g. on volatile days).
"""

from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

from mocktrade.domain.orders import Product
from mocktrade.positions.book import PositionBook
from mocktrade.recovery import IST
from mocktrade.risk.closing import ExitDesk, position_of

CUTOFFS: dict[str, time] = {"NSE": time(15, 20), "BSE": time(15, 20),
                            "NFO": time(15, 25), "BFO": time(15, 25)}  # fmt: skip
WARN_FROM = time(15, 0)


class SquareOff:
    def __init__(
        self,
        account_id: str,
        book: PositionBook,
        orders,  # OrderManager
        instruments,  # Callable[[str], Instrument | None]
        *,
        max_lots: int,
        cutoffs: dict[str, time] | None = None,
        warn_from: time = WARN_FROM,
        audit: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        desk: ExitDesk | None = None,  # the account's exit desk (#261)
    ) -> None:
        self.account_id = account_id
        self._book = book
        self._orders = orders
        self._instrument = instruments
        self.max_lots = max_lots  # closing pieces; an admin's edit replaces it
        self.cutoffs = dict(cutoffs or CUTOFFS)
        self.warn_from = warn_from
        self._audit = audit
        self._clock = clock
        self._desk = desk or ExitDesk(orders, position_of(book))

    def status(self) -> dict[str, Any]:
        """What the screens show: the cut-offs, the window, the open MIS positions."""
        now = self._clock().astimezone(IST)
        rows = []
        for p in self._book.positions():
            if p.product is not Product.MIS or not p.qty:
                continue
            exchange = p.instrument_id.split(":", 1)[0]
            cutoff = self.cutoffs.get(exchange)
            left = None
            if cutoff is not None:
                at = datetime.combine(now.date(), cutoff, IST)
                left = max(0, int((at - now).total_seconds() // 60))
            rows.append({"instrument_id": p.instrument_id, "qty": p.qty,
                         "cutoff": cutoff.strftime("%H:%M") if cutoff else None,
                         "minutes_left": left, "pnl_paise": p.pnl_paise})  # fmt: skip
        return {
            "account_id": self.account_id,
            "now": now.isoformat(),
            "warn_from": self.warn_from.strftime("%H:%M"),
            "cutoffs": {k: v.strftime("%H:%M") for k, v in sorted(self.cutoffs.items())},
            "window_open": self.warn_from <= now.time() < max(self.cutoffs.values()),
            "positions": rows,
        }

    def warning(self) -> str | None:
        """The alert text while the window is open and MIS positions are left, else None."""
        status = self.status()
        if not status["window_open"] or not status["positions"]:
            return None
        rows = status["positions"]
        soonest = min(
            (r["minutes_left"] for r in rows if r["minutes_left"] is not None), default=None
        )
        names = ", ".join(f"{r['instrument_id'].split(':', 1)[-1]} {r['qty']:+d}" for r in rows)
        when = f"in {soonest} min" if soonest is not None else "soon"
        many = "s" if len(rows) > 1 else ""
        return (f"{self.account_id}: {len(rows)} intraday (MIS) position{many} open ({names}); "
                f"the broker squares off {when}. Square off now?")  # fmt: skip

    async def close_all(self, user_id: str) -> list[dict[str, Any]]:
        """Close every open MIS position (the trader confirmed). Returns what was sent."""
        basket = f"SQ{self._clock().astimezone(IST).strftime('%H%M%S')}"
        done = []
        for p in [p for p in self._book.positions() if p.product is Product.MIS and p.qty]:
            instrument = self._instrument(p.instrument_id)
            if instrument is None:
                continue
            result = await self._desk.close(instrument, p.product, user_id=user_id,
                                            max_lots=self.max_lots, basket_id=basket,
                                            why=f"square-off by {user_id}")  # fmt: skip
            ids, refused = result.ids, result.refused
            done.append({"instrument_id": p.instrument_id, "qty": p.qty, "orders": ids,
                         "refused": refused, "left_open": result.left_open,
                         "stopped_algos": result.stopped_algos,
                         "cancelled": result.cancelled,
                         "summary": result.words()})  # fmt: skip
            if self._audit is not None:
                detail = f"{user_id} squared off {p.qty:+d} {p.instrument_id} MIS: {result.words()}"
                self._audit("SQUARE_OFF", detail, {"user_id": user_id, "orders": ids})
        return done
