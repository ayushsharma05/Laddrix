"""Alerts sent to a user's webhook: signed, retried, never on the trading loop's time.

    POST <the user's https URL>   {"type": "market_alert", "alert": {...}, "text": ..., "at": ...}
    X-MockTrade-Timestamp: 1760598000
    X-MockTrade-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>

The receiver recomputes the signature with its copy of the secret and refuses a stale
timestamp (e.g. older than five minutes): a copied request can't be replayed later.

Safety: https only, and the host may not be (or resolve to) a private, loopback, link-local
or otherwise non-public address, so a webhook can't be used to reach the server's own network.
Each send: a 5 s timeout, up to 4 tries (1 s, 2 s, 4 s apart) on a network error, a 429 or a
5xx; a 4xx is the receiver saying no, not tried again. The result goes back to the alert.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)
BACKOFF_S = (1.0, 2.0, 4.0)
MAX_PENDING = 500  # sends waiting at once; more are refused (said on the alert)

Resolver = Callable[[str], Awaitable[list[str]]]


class WebhookError(Exception):
    """A webhook that can't be used (said in words)."""


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def check_url(url: str) -> str:
    """The URL as given if it may be used (https, a public-looking host), else WebhookError."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebhookError("the webhook must be an https:// address")
    if parsed.username or parsed.password:
        raise WebhookError("the webhook address can't carry a user name or password")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise WebhookError("the webhook must be a public address")
    if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
        raise WebhookError("the webhook must be a public address")
    if len(url) > 500:
        raise WebhookError("the webhook address is too long")
    return url.strip()


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class WebhookSender:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = system_resolver,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
        self._resolver = resolver
        self._sleep = sleep
        self._now = now
        self._tasks: set[asyncio.Task] = set()
        self.sent = 0
        self.failed = 0

    def send(self, url: str, secret: str, payload: dict[str, Any],
             done: Callable[[str], None]) -> None:  # fmt: skip
        """Queue one send; `done("")` when it got through, `done(reason)` when it didn't."""
        if len(self._tasks) >= MAX_PENDING:
            done("too many webhook sends waiting: this one was dropped")
            return
        task = asyncio.ensure_future(self._deliver(url, secret, payload, done))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver_now(self, url: str, secret: str, payload: dict[str, Any]) -> str:
        """Send and wait for the result ("" when it got through): the "test" button."""
        result: list[str] = []
        await self._deliver(url, secret, payload, result.append)
        return result[0]

    async def _deliver(self, url: str, secret: str, payload: dict[str, Any],
                       done: Callable[[str], None]) -> None:  # fmt: skip
        error = ""
        try:
            check_url(url)
            await self._check_host(urlparse(url).hostname or "")
            body = json.dumps(payload, separators=(",", ":"), default=str).encode()
            for attempt in range(len(BACKOFF_S) + 1):
                error = await self._post(url, secret, body)
                if error is None:
                    error = ""
                    break
                if error.startswith("refused") or attempt == len(BACKOFF_S):
                    break
                await self._sleep(BACKOFF_S[attempt])
        except WebhookError as exc:
            error = str(exc)
        except Exception as exc:  # never let a webhook break anything else
            log.exception("webhook send failed")
            error = f"{type(exc).__name__}: {exc}"
        if error:
            self.failed += 1
            log.warning("webhook %s: %s", urlparse(url).hostname, error)
        else:
            self.sent += 1
        try:
            done(error)
        except Exception:
            log.exception("webhook result listener failed")

    async def _check_host(self, host: str) -> None:
        try:
            addresses = await self._resolver(host)
        except OSError as exc:
            raise WebhookError(f"the webhook host can't be found ({exc})") from exc
        if not addresses:
            raise WebhookError("the webhook host can't be found")
        for a in addresses:
            if not ipaddress.ip_address(a.split("%", 1)[0]).is_global:
                raise WebhookError("the webhook host is not a public address")

    async def _post(self, url: str, secret: str, body: bytes) -> str | None:
        """None when it got through; else why (a "refused …" reason is not tried again)."""
        ts = int(self._now())
        headers = {"content-type": "application/json", "x-mocktrade-timestamp": str(ts),
                   "x-mocktrade-signature": sign(secret, ts, body)}  # fmt: skip
        try:
            r = await self._client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return f"not reached: {type(exc).__name__}"
        if r.status_code < 300:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            return f"answered {r.status_code}"
        return f"refused: answered {r.status_code}"

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()
