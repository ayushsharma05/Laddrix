"""The live stream to the browser: one WebSocket per tab.

    prices    ─► per-connection subscription (newest wins) ─┐ at once if quiet, else at
    positions ─► newest per position                        ─┤ most once per 250 ms
    orders, kill switches, alerts ─► sent at once, in order ─┴─► queue ─► one sender ─► socket

Rules:
- Prices and positions go out **at once** when nothing was sent in the last 250 ms (the
  usual case: Kite sends about one tick per second per instrument), so we add no wait.
  In a burst they are conflated: at most one update per instrument (or position) per
  250 ms, the newest. Order events are NEVER conflated or dropped.
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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, NamedTuple

import orjson
from fastapi import WebSocket, WebSocketDisconnect

from mocktrade.algos.engine import AlgoEvent
from mocktrade.charges.model import ChargeBill, bill_to_dict
from mocktrade.charges.service import ChargesService
from mocktrade.domain.accounts import User, can_view
from mocktrade.domain.gtt import GttView, gtt_to_dict
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import Subscription
from mocktrade.metrics import METRICS, since_us
from mocktrade.ops.alerts import Alert, Alerts, alert_to_dict
from mocktrade.options.chain import ChainService, ChainWatch
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.orders.model import EventKind, OrderEvent
from mocktrade.platform import AccountRuntime, Platform
from mocktrade.portfolio.service import Snapshot, snapshot_to_dict
from mocktrade.positions.book import PositionView
from mocktrade.positions.reconcile import Mismatch
from mocktrade.risk.kill_switch import KillState
from mocktrade.server.formats import algo_to_dict, position_to_dict
from mocktrade.server.users import UserStore

log = logging.getLogger(__name__)

# Close codes (4000-4999 are ours to define)
CLOSE_NOT_LOGGED_IN = 4401
CLOSE_WRONG_ORIGIN = 4403
CLOSE_TOO_SLOW = 4408

NOT_SENT = {EventKind.IGNORED}  # audit-only events stay in the event log


MAX_CHAINS = 2  # option chains streaming per screen connection


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
    positions_changed: asyncio.Event = field(default_factory=asyncio.Event)
    chains: dict[tuple[str, str], asyncio.Task] = field(default_factory=dict)  # option chains

    def put(self, message: "dict[str, Any] | _Raw", since_ns: int = 0) -> None:
        """Queue a message; `since_ns` is when its cause happened (default: now)."""
        if self.too_slow.is_set():
            return
        try:
            self.queue.put_nowait((since_ns or time.perf_counter_ns(), message))
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
        clock: Callable[[], float] = time.monotonic,
        alerts: Alerts | None = None,
        chains: ChainService | None = None,
    ) -> None:
        self._clock = clock
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
        self._alerts = alerts
        self._chains = chains
        self.chain_interval_s = 1.0
        if alerts is not None:
            alerts.subscribe(self._on_ops)

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
        runtime.algos.subscribe(self._on_algo)
        if runtime.gtt is not None:
            runtime.gtt.subscribe(self._on_gtt)
        if runtime.portfolio is not None:
            runtime.portfolio.subscribe(self._on_portfolio)
        if runtime.charges is not None:
            charges = runtime.charges
            charges.subscribe(lambda account_id, bill: self._on_charges(account_id, bill, charges))

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
        for alert in self._alerts.active() if self._alerts else ():
            connection.put(alert_to_dict(alert))  # what's wrong right now, at once
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
            for chain in connection.chains.values():
                chain.cancel()

    async def _send(self, websocket: WebSocket, connection: _Connection) -> None:
        while True:
            since_ns, message = await connection.queue.get()
            if isinstance(message, _Raw):
                kind = message.kind
                await websocket.send_text(message.text)
            else:
                kind = message.get("type")
                await websocket.send_text(json.dumps(message, separators=(",", ":")))
            # ticks: from arriving at the server to on the wire (includes conflation waits);
            # everything else: from the event to on the wire
            name = "stream_tick_age_us" if kind == "ticks" else f"stream_send_us{{type={kind}}}"
            METRICS.observe(name, since_us(since_ns))

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
                elif op in ("chain", "chain_stop"):
                    self._change_chain(connection, op, message)
                else:
                    connection.put({"type": "error", "message": "unknown message"})

    def _change_chain(self, connection: _Connection, op: str, message: dict) -> None:
        """Start or stop an option chain: {"op": "chain", "underlying", "expiry", "strikes"}."""
        underlying, expiry_text = message.get("underlying"), message.get("expiry")
        key = (str(underlying), str(expiry_text))
        old = connection.chains.pop(key, None)
        if old is not None:
            old.cancel()
        if op == "chain_stop":
            return
        try:
            expiry = date.fromisoformat(str(expiry_text))
            strikes = int(message.get("strikes", 15))
        except (TypeError, ValueError):
            connection.put({"type": "error", "message": "chain needs an expiry date and strikes"})
            return
        if self._chains is None or expiry not in self._chains.expiries(str(underlying)):
            connection.put({"type": "error", "message": f"no options on {underlying} {expiry}"})
            return
        if len(connection.chains) >= MAX_CHAINS:
            connection.put({"type": "error", "message": f"at most {MAX_CHAINS} chains at a time"})
            return
        watch = self._chains.watch(str(underlying), expiry, max(1, min(strikes, 40)))
        connection.chains[key] = asyncio.create_task(self._stream_chain(connection, watch))

    async def _stream_chain(self, connection: _Connection, watch: ChainWatch) -> None:
        """The chain at once, then at most once a second while anything in it changes:
        IVs and Greeks don't need tick speed, and a 60-row chain is a big message."""
        try:
            while True:
                connection.put({"type": "chain", **watch.snapshot()})
                await asyncio.sleep(self.chain_interval_s)
                await watch.subscription.wait()
        finally:
            watch.close()

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
        """Send price and position changes as soon as they come, but never more than once per
        flush interval: the first change after a quiet spell goes out at once (no added
        latency); changes during a burst wait for the rest of the interval and only the
        newest is sent (a slow browser is never flooded)."""
        last = float("-inf")
        while True:
            await self._changed(connection)
            wait = last + self.flush_s - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)  # a burst: collect, newest wins
            last = self._clock()
            since = connection.ticks.pending_since_ns
            ticks = connection.ticks.drain()
            if ticks:
                # each tick is encoded once and shared by every screen watching it
                body = ",".join([tick_json(t) for t in ticks])
                connection.put(_Raw("ticks", f'{{"type":"ticks","ticks":[{body}]}}'), since)
            connection.positions_changed.clear()
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

    @staticmethod
    async def _changed(connection: _Connection) -> None:
        """Wait until a price or a position has something new for this connection."""
        if connection.positions:
            return
        waits = [
            asyncio.ensure_future(connection.ticks.wait()),
            asyncio.ensure_future(connection.positions_changed.wait()),
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()

    async def _watch_session(self, connection: _Connection) -> None:
        """Returns (ending the stream) once the session is gone: logout, expiry, disabling."""
        while True:
            await asyncio.sleep(self._recheck_s)
            user = self._users.session_user(connection.token)
            if user is None:
                return
            connection.accounts = self._viewable(user)  # permissions can change

    # --- events from the platform ------------------------------------------------------------

    def _on_ops(self, alert: Alert) -> None:
        message = alert_to_dict(alert)
        for connection in list(self._connections):
            connection.put(message)  # operations concern everyone signed in

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
                connection.positions_changed.set()

    def _on_algo(self, event: AlgoEvent) -> None:
        """Algo changes: like order events, never conflated or dropped."""
        message = {
            "type": "algo",
            "account_id": event.algo.account_id,
            "kind": event.kind,
            "detail": event.detail,
            "algo": algo_to_dict(event.algo),
        }
        for connection in list(self._connections):
            if event.algo.account_id in connection.accounts:
                connection.put(message)

    def _on_gtt(self, view: GttView) -> None:
        """GTT changes (placed, changed, fired, deleted): never conflated or dropped."""
        account_id = view.request.account_id
        message = {"type": "gtt", "account_id": account_id, "gtt": gtt_to_dict(view)}
        for connection in list(self._connections):
            if account_id in connection.accounts:
                connection.put(message)

    def _on_portfolio(self, account_id: str, snapshot: Snapshot) -> None:
        """Funds, holdings or trades changed: the whole list (they're short), never dropped."""
        message = {"type": "portfolio", **snapshot_to_dict(account_id, snapshot)}
        for connection in list(self._connections):
            if account_id in connection.accounts:
                connection.put(message)

    def _on_charges(self, account_id: str, bill: ChargeBill, service: ChargesService) -> None:
        """The day's charges after a fill (positions show P&L gross and net of them)."""
        message = {"type": "charges", "account_id": account_id,
                   **bill_to_dict(bill, service.model.schedule)}  # fmt: skip
        for connection in list(self._connections):
            if account_id in connection.accounts:
                connection.put(message)

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


class _Raw(NamedTuple):
    """A message already encoded (ticks: shared between screens)."""

    kind: str
    text: str


_encoded: dict[str, tuple[Tick, str]] = {}  # instrument -> (its newest tick, that tick's JSON)


def tick_json(tick: Tick) -> str:
    """The tick as JSON, encoded once however many screens watch it."""
    cached = _encoded.get(tick.instrument_id)
    if cached is not None and cached[0] is tick:
        return cached[1]
    # orjson: about 4x faster than json here; depth levels go out as [price, qty, orders]
    text = orjson.dumps(_tick(tick), default=tuple).decode()
    _encoded[tick.instrument_id] = (tick, text)
    return text


def _tick(tick: Tick) -> dict[str, Any]:
    """The tick's fields for the screens (datetimes and depth are encoded by orjson)."""
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
        "exchange_time": tick.exchange_time,
        "bids": tick.bids,
        "asks": tick.asks,
    }
