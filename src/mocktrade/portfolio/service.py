"""What an account holds and owns, kept fresh from its broker: funds, holdings, trades.

    broker ports (optional) ──read──► PortfolioService ──► REST, stream {"type": "portfolio"}
      FundsSource.funds()              │ every `refresh_s`, on demand (fresh=1),
      HoldingsSource.holdings()        │ and `after_fill_s` after a fill (debounced)
      TradesSource.trades()            └ holdings re-marked with live prices every `mark_s`

Rules (each a real failure):
- **A failed read never empties a screen.** The last good figures stay, with when they were
  read (`as_of`) and why the refresh failed (`error`); a screen says "as of 10:31:05".
- **One read at a time per kind.** Ten screens asking at once make one broker call (Kite
  allows a few reads a second; the order endpoints share nothing with these).
- **Pushed only when something changed**, so a quiet account sends nothing.
- **Our order ids on the tradebook.** Kite's /trades doesn't carry our tag: a trade's broker
  order id is looked up in the order manager; trades from orders placed elsewhere (Kite web,
  the phone app) keep an empty client id, and screens say so.
- **Holdings at live prices.** The broker's last price is a snapshot; holdings are re-marked
  from our market data as ticks arrive (their instruments are subscribed for that).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from mocktrade.broker.base import BrokerError, FundsSource, HoldingsSource, TradesSource
from mocktrade.domain.portfolio import (
    Holding,
    Trade,
    funds_to_dict,
    holding_to_dict,
    trade_to_dict,
)
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import EventKind, OrderEvent

log = logging.getLogger(__name__)

KINDS = ("funds", "holdings", "trades")
PORTS = {"funds": FundsSource, "holdings": HoldingsSource, "trades": TradesSource}
WHAT = {"funds": "funds and margins", "holdings": "holdings", "trades": "a tradebook"}


class PortfolioError(Exception):
    """Not offered by this broker (the API answers 404 with this message)."""


@dataclass(frozen=True)
class Snapshot:
    kind: str
    items: tuple[Any, ...]  # Funds | Holding | Trade
    as_of: datetime | None  # when the broker last answered; None if it never has
    error: str = ""  # why the latest refresh failed ("" when it worked)


def snapshot_to_dict(account_id: str, s: Snapshot) -> dict[str, Any]:
    convert = {"funds": funds_to_dict, "holdings": holding_to_dict, "trades": trade_to_dict}
    return {
        "account_id": account_id,
        "kind": s.kind,
        "items": [convert[s.kind](item) for item in s.items],
        "as_of": s.as_of.isoformat() if s.as_of else None,
        "error": s.error,
    }


class PortfolioService:
    def __init__(
        self,
        account_id: str,
        broker: object,
        market_data: MarketData,
        *,
        client_id_of: Callable[[str], str | None] = lambda _broker_id: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        refresh_s: float = 30.0,
        after_fill_s: float = 0.5,
        mark_s: float = 2.0,
    ) -> None:
        self.account_id = account_id
        self._broker = broker
        self._market_data = market_data
        self._client_id_of = client_id_of
        self._clock = clock
        self.refresh_s = refresh_s
        self.after_fill_s = after_fill_s
        self.mark_s = mark_s
        self.offered = tuple(k for k in KINDS if isinstance(broker, PORTS[k]))
        self._snapshots: dict[str, Snapshot] = {}
        self._reading: dict[str, asyncio.Task] = {}
        self._fill_timer: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[str, Snapshot], None]] = []
        self._ticks = market_data.subscribe()

    # --- reading ------------------------------------------------------------------------------

    def offers(self, kind: str) -> bool:
        return kind in self.offered

    def snapshot(self, kind: str) -> Snapshot | None:
        return self._snapshots.get(kind)

    async def read(self, kind: str, *, fresh: bool = False) -> Snapshot:
        """The latest figures; asks the broker first if `fresh` or never asked yet."""
        self._check(kind)
        if fresh or kind not in self._snapshots:
            await self.refresh(kind)
        return self._snapshots[kind]

    def subscribe(self, listener: Callable[[str, Snapshot], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- keeping fresh ----------------------------------------------------------------------------

    async def refresh(self, kind: str) -> None:
        """Ask the broker now; joins a read already under way instead of starting another."""
        self._check(kind)
        task = self._reading.get(kind)
        if task is None or task.done():
            task = asyncio.ensure_future(self._fetch(kind))
            self._reading[kind] = task
        await asyncio.shield(task)

    async def refresh_all(self) -> None:
        await asyncio.gather(*(self.refresh(k) for k in self.offered))

    def on_order_event(self, event: OrderEvent) -> None:
        """Order manager listener: after a fill, money, trades and holdings change soon."""
        if event.kind is not EventKind.FILL or event.account_id != self.account_id:
            return
        if not self.offered:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._fill_timer is not None:
            self._fill_timer.cancel()  # a burst of fills makes one read, after the last
        self._fill_timer = loop.call_later(
            self.after_fill_s, lambda: asyncio.ensure_future(self._after_fill())
        )

    async def run(self) -> None:
        """Refresh every `refresh_s`, re-mark holdings every `mark_s`, until cancelled."""
        if not self.offered:
            return
        last_refresh = None
        while True:
            now = asyncio.get_running_loop().time()
            if last_refresh is None or now - last_refresh >= self.refresh_s:
                last_refresh = now
                await self._quietly(self.refresh_all())
            self.mark()
            await asyncio.sleep(self.mark_s)

    def mark(self) -> None:
        """Holdings at the latest prices; pushed when a value moved."""
        self._ticks.drain()  # the subscription only keeps their prices coming
        old = self._snapshots.get("holdings")
        if old is None:
            return
        marked = tuple(self._marked(h) for h in old.items)
        if marked != old.items:
            self._store(replace(old, items=marked))

    # --- inside -------------------------------------------------------------------------------

    def _check(self, kind: str) -> None:
        if kind not in KINDS:
            raise PortfolioError(f"unknown kind {kind!r}")
        if kind not in self.offered:
            name = getattr(self._broker, "name", "this broker")
            raise PortfolioError(f"{name} doesn't report {WHAT[kind]}")

    async def _after_fill(self) -> None:
        self._fill_timer = None
        await self._quietly(self.refresh_all())

    @staticmethod
    async def _quietly(work) -> None:
        try:
            await work
        except Exception:
            log.exception("portfolio refresh failed")

    async def _fetch(self, kind: str) -> None:
        old = self._snapshots.get(kind)
        try:
            items = tuple(await getattr(self._broker, kind)())
        except BrokerError as exc:
            error = exc.reason
        except Exception as exc:  # a read: nothing was changed at the broker
            log.warning("%s for %s: %r", kind, self.account_id, exc)
            error = f"no answer from the broker ({type(exc).__name__})"
        else:
            if kind == "holdings":
                items = tuple(self._marked(h) for h in items)
                self._ticks.add(h.instrument_id for h in items)
            elif kind == "trades":
                items = tuple(self._ours(t) for t in items)
            self._store(Snapshot(kind, items, self._clock()), compare=old)
            return
        # keep the last good figures, say why they're old
        self._store(Snapshot(kind, old.items if old else (), old.as_of if old else None, error),
                    compare=old)  # fmt: skip

    def _store(self, snapshot: Snapshot, compare: Snapshot | None = None) -> None:
        before = compare if compare is not None else self._snapshots.get(snapshot.kind)
        self._snapshots[snapshot.kind] = snapshot
        if before is not None and (before.items, before.error) == (snapshot.items, snapshot.error):
            return  # nothing a screen would show differently (as_of alone isn't pushed)
        for listener in list(self._listeners):
            try:
                listener(self.account_id, snapshot)
            except Exception:
                log.exception("portfolio listener failed")

    def _marked(self, h: Holding) -> Holding:
        tick = self._market_data.latest(h.instrument_id)
        if tick is None or tick.ltp_paise <= 0:
            return h
        return replace(h, ltp_paise=tick.ltp_paise, close_paise=tick.close_paise or h.close_paise)

    def _ours(self, t: Trade) -> Trade:
        if t.client_order_id:
            return t
        return replace(t, client_order_id=self._client_id_of(t.broker_order_id) or "")
