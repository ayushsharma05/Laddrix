"""Kite's live WebSocket: prices (binary) and order updates (JSON text), kept alive forever.

Behaviour a professional feed handler needs:
- Reconnect after any drop, waiting 1 s, 2 s, 4 s ... up to 30 s (exponential backoff),
  and reset the wait once a connection works.
- Resubscribe everything after each reconnect.
- Treat silence as a failure: Kite sends data or a heartbeat every few seconds, so
  if nothing arrives for `stale_after_s` we drop the connection and reconnect.
- Stop retrying on an authentication failure (expired token): retrying can't help.

Limits (https://kite.trade/docs/connect/v3/websocket/): 3000 instruments per
connection, 3 connections per API key.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from decimal import Decimal
from typing import Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus

from mocktrade.broker.kite.decoder import DecodeError, KiteTick, decode_frame
from mocktrade.broker.kite.http import KiteAuthError

log = logging.getLogger(__name__)

WS_URL = "wss://ws.kite.trade"
MAX_TOKENS_PER_CONNECTION = 3000
MODES = ("ltp", "quote", "full")

TicksHandler = Callable[[list[KiteTick]], None]
OrderHandler = Callable[[dict[str, Any]], None]


class KiteTicker:
    def __init__(
        self,
        api_key: str,
        access_token: str,
        on_ticks: TicksHandler,
        on_order: OrderHandler | None = None,
        *,
        on_connect: Callable[[], None] | None = None,
        url: str = WS_URL,
        connect: Callable[..., Any] = ws_connect,
        stale_after_s: float = 10.0,
        max_backoff_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = f"{url}?api_key={api_key}&access_token={access_token}"
        self._on_ticks = on_ticks
        self._on_order = on_order
        self._on_connect = on_connect  # e.g. reconcile orders: updates may have been missed
        self._connect = connect
        self._stale_after_s = stale_after_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep
        self._modes: dict[int, str] = {}  # token -> mode, the subscriptions to keep
        self._ws: Any = None
        self._stopped = False
        self.connected = asyncio.Event()
        self.connections = 0  # how many times we've connected (for monitoring)

    @property
    def subscriptions(self) -> dict[int, str]:
        return dict(self._modes)

    async def subscribe(self, tokens: Iterable[int], mode: str = "full") -> None:
        """Add instruments (or change their mode). Applied now if connected, else on connect."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        new = {int(t): mode for t in tokens}
        if len(self._modes.keys() | new.keys()) > MAX_TOKENS_PER_CONNECTION:
            raise ValueError(f"a Kite connection holds at most {MAX_TOKENS_PER_CONNECTION} tokens")
        self._modes.update(new)
        if self._ws is not None and new:
            await self._send_subscriptions(new)

    async def unsubscribe(self, tokens: Iterable[int]) -> None:
        gone = [int(t) for t in tokens if int(t) in self._modes]
        for token in gone:
            del self._modes[token]
        if self._ws is not None and gone:
            await self._ws.send(json.dumps({"a": "unsubscribe", "v": gone}))

    def stop(self) -> None:
        """Ask `run` to finish. The current connection is closed."""
        self._stopped = True
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    async def run(self) -> None:
        """Connect and keep connected until `stop()` (or an authentication failure)."""
        backoff = 1.0
        while not self._stopped:
            try:
                async with self._connect(self._url) as ws:
                    self._ws = ws
                    self.connections += 1
                    self.connected.set()
                    backoff = 1.0
                    log.info("kite ticker connected (%d instruments)", len(self._modes))
                    await self._send_subscriptions(self._modes)
                    self._call_safely(self._on_connect)
                    await self._read_until_closed(ws)
            except InvalidStatus as exc:
                if exc.response.status_code in (401, 403):
                    self._stopped = True
                    raise KiteAuthError("Kite refused the WebSocket: log in again") from exc
                log.warning("kite ticker refused: HTTP %s", exc.response.status_code)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors of every kind: reconnect
                log.warning("kite ticker dropped: %s", exc)
            finally:
                self._ws = None
                self.connected.clear()
            if self._stopped:
                break
            await self._sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff_s)

    async def _read_until_closed(self, ws: Any) -> None:
        while not self._stopped:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=self._stale_after_s)
            except TimeoutError:
                raise ConnectionError(f"no data for {self._stale_after_s} s") from None
            if isinstance(message, bytes):
                self._handle_binary(message)
            else:
                self._handle_text(message)

    def _handle_binary(self, frame: bytes) -> None:
        try:
            ticks = decode_frame(frame)
        except DecodeError as exc:
            log.error("bad tick frame dropped: %s", exc)
            return
        if ticks:
            self._on_ticks(ticks)

    def _handle_text(self, text: str) -> None:
        try:
            message = json.loads(text, parse_float=Decimal)  # prices stay exact
        except ValueError:
            log.warning("unreadable text message from kite")
            return
        kind, data = message.get("type"), message.get("data")
        if kind == "order" and self._on_order is not None:
            self._call_safely(self._on_order, data)
        elif kind == "error":
            log.error("kite ticker error: %s", data)
        elif kind == "message":
            log.info("kite message: %s", data)

    @staticmethod
    def _call_safely(callback: Callable[..., None] | None, *args: Any) -> None:
        """A failing callback is logged; it must never drop the price connection."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("kite ticker callback failed")

    async def _send_subscriptions(self, modes: dict[int, str]) -> None:
        if not modes:
            return
        await self._ws.send(json.dumps({"a": "subscribe", "v": sorted(modes)}))
        by_mode: dict[str, list[int]] = {}
        for token, mode in modes.items():
            by_mode.setdefault(mode, []).append(token)
        for mode, tokens in sorted(by_mode.items()):
            await self._ws.send(json.dumps({"a": "mode", "v": [mode, sorted(tokens)]}))
