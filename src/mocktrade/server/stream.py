"""The live stream to the browser: one WebSocket per tab.

    prices    ─► per-connection subscription (newest wins) ─┐ every 250 ms: one "ticks"
    positions ─► newest per position                        ─┤ and one "positions" batch
    orders, kill switches, alerts ─► sent at once, in order ─┴─► queue ─► one sender ─► socket

Rules:
- Prices and positions are conflated: at most one update per instrument (or
  position) per 250 ms, the newest. Order events are NEVER conflated or dropped.
- Each connection has a bounded queue. A browser too slow to keep up is
  disconnected ("too slow"), never silently skipped: the page reconnects and loads
  fresh snapshots from the API, so nothing is ever missing without it knowing.
- The handshake needs our Origin (blocks cross-site WebSocket hijacking) and a valid
  session. The session is re-checked every minute; logging out closes the stream.
- Everyone sees only the accounts they may view.
Message formats: docs/design/stream.md.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from mocktrade.domain.accounts import User, can_view
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import Subscription
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.orders.model import EventKind, OrderEvent
from mocktrade.platform import AccountRuntime, Platform
from mocktrade.positions.book import PositionView
from mocktrade.positions.reconcile import Mismatch
from mocktrade.risk.kill_switch import KillState
from mocktrade.server.formats import position_to_dict
from mocktrade.server.users import UserStore

log = logging.getLogger(__name__)

# Close codes (4000-4999 are ours to define)
CLOSE_NOT_LOGGED_IN = 4401
CLOSE_WRONG_ORIGIN = 4403
CLOSE_TOO_SLOW = 4408

NOT_SENT = {EventKind.IGNORED}  # audit-only events stay in the event log


@dataclass(eq=False)  # each connection is its own object: compared by identity
class _Connection:
    user: User
    token: str
    accounts: set[str]  # account ids this user may view
    queue: asyncio.Queue
    ticks: Subscription
    positions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    pnl: dict[str, int] = field(default_factory=dict)  # account -> day P&L, newest
    too_slow: asyncio.Event = field(default_factory=asyncio.Event)

    def put(self, message: dict[str, Any]) -> None:
        if self.too_slow.is_set():
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.too_slow.set()  # disconnect rather than skip anything


class StreamHub:
    def __init__(
        self,
        platform: Platform,
        users: UserStore,
        *,
        origin: str,
        cookie_name: str,
        flush_s: float = 0.25,
        queue_limit: int = 2000,
        max_instruments: int = 200,
        recheck_s: float = 60.0,
    ) -> None:
        self._platform = platform
        self._users = users
        self._origin = origin
        self._cookie = cookie_name
        self.flush_s = flush_s
        self._queue_limit = queue_limit
        self._max_instruments = max_instruments
        self._recheck_s = recheck_s
        self._connections: set[_Connection] = set()
        self._attached: set[str] = set()
        platform.firm_switch.on_change(self._on_kill)
        for runtime in platform.accounts.values():
            self.attach(runtime)

    @property
    def connections(self) -> int:
        return len(self._connections)

    def attach(self, runtime: AccountRuntime) -> None:
        """Listen to one account's orders, positions, kill switch and alerts."""
        account_id = runtime.account.id
        if account_id in self._attached:
            return
        self._attached.add(account_id)
        runtime.orders.subscribe(self._on_order)
        runtime.positions.subscribe(lambda view: self._on_position(account_id, runtime, view))
        runtime.switch.on_change(self._on_kill)
        runtime.reconciler.subscribe(
            lambda what, mismatch: self._on_alert(account_id, what, mismatch)
        )

    # --- one browser tab ----------------------------------------------------------------------

    async def serve(self, websocket: WebSocket) -> None:
        if websocket.headers.get("origin") != self._origin:
            await websocket.close(code=CLOSE_WRONG_ORIGIN)
            return
        token = websocket.cookies.get(self._cookie, "")
        user = self._users.session_user(token)
        if user is None:
            await websocket.close(code=CLOSE_NOT_LOGGED_IN)
            return
        await websocket.accept()
        connection = _Connection(
            user=user,
            token=token,
            accounts=self._viewable(user),
            queue=asyncio.Queue(self._queue_limit),
            ticks=self._platform.market_data.subscribe(),
        )
        self._connections.add(connection)
        connection.put(
            {
                "type": "hello",
                "user": user.id,
                "accounts": sorted(connection.accounts),
                "flush_ms": int(self.flush_s * 1000),
                "max_instruments": self._max_instruments,
            }
        )
        tasks = [
            asyncio.create_task(self._send(websocket, connection)),
            asyncio.create_task(self._receive(websocket, connection)),
            asyncio.create_task(self._flush(connection)),
            asyncio.create_task(self._watch_session(connection)),
            asyncio.create_task(connection.too_slow.wait()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            code = 1000
            if connection.too_slow.is_set():
                code = CLOSE_TOO_SLOW
            elif tasks[3] in done:
                code = CLOSE_NOT_LOGGED_IN
            with contextlib.suppress(Exception):
                await websocket.close(code=code)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._connections.discard(connection)
            connection.ticks.close()

    async def _send(self, websocket: WebSocket, connection: _Connection) -> None:
        while True:
            message = await connection.queue.get()
            await websocket.send_text(json.dumps(message, separators=(",", ":")))

    async def _receive(self, websocket: WebSocket, connection: _Connection) -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                text = await websocket.receive_text()
                try:
                    message = json.loads(text)
                    op = message.get("op") if isinstance(message, dict) else None
                except ValueError:
                    op = None
                if op == "ping":
                    connection.put({"type": "pong"})
                elif op in ("subscribe", "unsubscribe"):
                    self._change_subscription(connection, op, message.get("instruments"))
                else:
                    connection.put({"type": "error", "message": "unknown message"})

    def _change_subscription(self, connection: _Connection, op: str, ids: Any) -> None:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            connection.put({"type": "error", "message": "instruments must be a list of ids"})
            return
        if op == "unsubscribe":
            connection.ticks.remove(ids)
            return
        known = [i for i in ids if self._platform.instruments.get(i) is not None]
        unknown = sorted(set(ids) - set(known))
        if unknown:
            connection.put({"type": "error", "message": f"unknown instruments: {unknown}"})
        room = self._max_instruments - len(connection.ticks.instrument_ids)
        new = [i for i in dict.fromkeys(known) if i not in connection.ticks.instrument_ids]
        if len(new) > room:
            connection.put(
                {
                    "type": "error",
                    "message": f"at most {self._max_instruments} instruments per connection",
                }
            )
            new = new[: max(room, 0)]
        connection.ticks.add(new)

    async def _flush(self, connection: _Connection) -> None:
        """Every flush interval: the newest price per instrument, the newest positions."""
        while True:
            await asyncio.sleep(self.flush_s)
            ticks = connection.ticks.drain()
            if ticks:
                connection.put({"type": "ticks", "ticks": [_tick(t) for t in ticks]})
            if connection.positions:
                by_account: dict[str, list[dict[str, Any]]] = {}
                for (account_id, _, _), position in connection.positions.items():
                    by_account.setdefault(account_id, []).append(position)
                connection.positions.clear()
                for account_id, positions in sorted(by_account.items()):
                    connection.put(
                        {
                            "type": "positions",
                            "account_id": account_id,
                            "positions": positions,
                            "day_pnl_paise": connection.pnl.get(account_id, 0),
                        }
                    )

    async def _watch_session(self, connection: _Connection) -> None:
        """Returns (ending the stream) once the session is gone: logout, expiry, disabling."""
        while True:
            await asyncio.sleep(self._recheck_s)
            user = self._users.session_user(connection.token)
            if user is None:
                return
            connection.accounts = self._viewable(user)  # permissions can change

    # --- events from the platform ------------------------------------------------------------

    def _on_order(self, event: OrderEvent) -> None:
        if event.kind in NOT_SENT:
            return
        message = {
            "type": "order",
            "account_id": event.account_id,
            "kind": event.kind.value,
            "detail": event.detail,
            "order": view_to_dict(event.order) if event.order else None,
            "fill": fill_to_dict(event.fill) if event.fill else None,
        }
        for connection in list(self._connections):
            if event.account_id in connection.accounts:
                connection.put(message)

    def _on_position(self, account_id: str, runtime: AccountRuntime, view: PositionView) -> None:
        if not self._connections:
            return
        data = position_to_dict(view)
        pnl = runtime.positions.day_pnl_paise()
        key = (account_id, view.instrument_id, view.product.value)
        for connection in list(self._connections):
            if account_id in connection.accounts:
                connection.positions[key] = data  # newest wins
                connection.pnl[account_id] = pnl

    def _on_kill(self, name: str, state: KillState) -> None:
        message = {
            "type": "kill",
            "scope": name,
            "engaged": state.engaged,
            "reason": state.reason,
            "by": state.by,
            "at": state.at.isoformat() if state.at else None,
        }
        for connection in list(self._connections):
            account_id = name.removeprefix("account ")
            if name == "firm" or account_id in connection.accounts:
                connection.put(message)

    def _on_alert(self, account_id: str, what: str, mismatch: Mismatch) -> None:
        message = {
            "type": "position_alert",
            "account_id": account_id,
            "state": what,
            "instrument_id": mismatch.instrument_id,
            "ours": mismatch.ours,
            "broker": mismatch.broker,
            "message": mismatch.message,
        }
        for connection in list(self._connections):
            if account_id in connection.accounts:
                connection.put(message)

    def _viewable(self, user: User) -> set[str]:
        return {a.id for a in self._users.accounts() if can_view(user, a)}


def _tick(tick: Tick) -> dict[str, Any]:
    return {
        "instrument_id": tick.instrument_id,
        "ltp_paise": tick.ltp_paise,
        "last_qty": tick.last_qty,
        "volume": tick.volume,
        "avg_price_paise": tick.avg_price_paise,
        "buy_qty": tick.buy_qty,
        "sell_qty": tick.sell_qty,
        "open_paise": tick.open_paise,
        "high_paise": tick.high_paise,
        "low_paise": tick.low_paise,
        "close_paise": tick.close_paise,
        "open_interest": tick.open_interest,
        "exchange_time": tick.exchange_time.isoformat() if tick.exchange_time else None,
        "bids": [[level.price_paise, level.qty, level.orders] for level in tick.bids],
        "asks": [[level.price_paise, level.qty, level.orders] for level in tick.asks],
    }
