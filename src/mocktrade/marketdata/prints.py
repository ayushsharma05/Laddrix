"""Time & sales: the trades between ticks, taken from the tick stream (a MarketData tap).

Kite doesn't send trades one by one, only snapshots carrying the day's total volume, the last
price and the book. So a "print" is what traded between two snapshots: the volume difference
at the newer last price, at the newer last-trade time. That is an estimate (two trades in one
snapshot show as one), the same one volume at price makes (vap.py).

The side is estimated from the touch *before* the trade: at or above the previous best ask, a
buyer lifted the offer (BUY); at or below the previous best bid, a seller hit the bid (SELL);
in between, unknown (""). Screens label it as an estimate.

Kept only for instruments a screen watches (`wanted`), the newest `keep` prints each.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.ticks import Tick


@dataclass(frozen=True, slots=True)
class Print:
    seq: int  # per instrument, rising: "everything after n" for a screen catching up
    at: datetime | None
    price_paise: int
    qty: int
    side: str  # BUY | SELL | "" (unknown)


def print_to_dict(p: Print) -> dict[str, Any]:
    return {"seq": p.seq, "t": p.at.isoformat() if p.at else None, "price_paise": p.price_paise,
            "qty": p.qty, "side": p.side}  # fmt: skip


@dataclass(slots=True)
class _Last:
    volume: int
    bid: int | None
    ask: int | None


class TimeAndSales:
    def __init__(
        self, *, keep: int = 300, wanted: Callable[[InstrumentId], bool] = lambda i: True
    ) -> None:
        self.keep = keep
        self._wanted = wanted
        self._last: dict[InstrumentId, _Last] = {}
        self._prints: dict[InstrumentId, deque[Print]] = {}
        self._seq: dict[InstrumentId, int] = {}

    def on_tick(self, tick: Tick) -> None:
        """MarketData tap: a dict lookup; a print only when volume moved."""
        if tick.volume is None:
            return
        i = tick.instrument_id
        before = self._last.get(i)
        bid = tick.bids[0].price_paise if tick.bids else None
        ask = tick.asks[0].price_paise if tick.asks else None
        self._last[i] = _Last(tick.volume, bid, ask)
        if before is None or tick.volume <= before.volume:
            return  # first sight, nothing traded, or a new day (the total started again)
        if not self._wanted(i):
            self._prints.pop(i, None)
            return
        price = tick.ltp_paise
        side = ""
        if before.ask is not None and price >= before.ask:
            side = "BUY"
        elif before.bid is not None and price <= before.bid:
            side = "SELL"
        seq = self._seq.get(i, 0) + 1
        self._seq[i] = seq
        tape = self._prints.setdefault(i, deque(maxlen=self.keep))
        tape.append(Print(seq, tick.last_trade_time or tick.exchange_time or tick.received_at,
                          price, tick.volume - before.volume, side))  # fmt: skip

    def recent(self, instrument_id: InstrumentId, after: int = 0, limit: int = 100) -> list[Print]:
        """The newest prints (oldest first), only those after `after` when given."""
        tape = self._prints.get(instrument_id)
        if not tape:
            return []
        fresh = [p for p in tape if p.seq > after]
        return fresh[-limit:]
