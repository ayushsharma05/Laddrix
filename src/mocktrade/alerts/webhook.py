"""Alerts sent to a user's webhook: signed, retried, never on the trading loop's time.

    POST <the user's https URL>   {"type": "market_alert", "alert": {...}, "text": ..., "at": ...}
    X-MockTrade-Timestamp: 1760598000
    X-MockTrade-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>

The receiver recomputes the signature with its copy of the secret and refuses a stale
timestamp (e.g. older than five minutes): a copied request can't be replayed later.

Safety: https only, and the host may not be (or resolve to) a private, loopback, link-local
or otherwise non-public address, so a webhook can't be used to reach the server's own network.

    each try ─► resolve the host ─► every address public? ─no─► refused
                                        │ yes
                                        ▼
               connect to THAT address (never a second lookup), TLS checked against the host name

The address checked is the address connected to (#271): a host that answers a public address
to the check and a private one to the connection ("DNS rebinding") can't slip through. Each try
looks the host up and checks it again. A redirect is not followed. IPv4 written as IPv6
(::ffff:10.0.0.1) is judged as the IPv4 it is. Nothing is pooled across sends, so a connection
made for one host name is never reused for another.

Each send: a 5 s timeout, up to 4 tries (1 s, 2 s, 4 s apart) on a network error, a 429 or a
5xx; a 4xx is the receiver saying no, not tried again. The result goes back to the alert. At
most MAX_PENDING sends wait at once (queued and "test" sends together), and at most
MAX_IN_FLIGHT connect at once.
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
MAX_IN_FLIGHT = 20  # connections open at once

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
    if ip is not None and not public(str(ip)):
        raise WebhookError("the webhook must be a public address")
    if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
        raise WebhookError("the webhook must be a public address")
    if len(url) > 500:
        raise WebhookError("the webhook address is too long")
    return url.strip()


def public(address: str) -> bool:
    """A public internet address (an IPv6 zone dropped; IPv4 written as IPv6 judged as IPv4)."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def pinned(url: str, address: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """The request to `url` sent to `address`: the URL with the address as its host, the Host
    header and the TLS name (SNI, and the certificate check) still the URL's own host."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    where = f"[{ip}]" if ip.version == 6 else str(ip)
    port = f":{parsed.port}" if parsed.port else ""
    target = parsed._replace(netloc=where + port).geturl()
    return target, {"host": host + port}, {"sni_hostname": host}


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
        self._client = client or httpx.AsyncClient(
            timeout=5, follow_redirects=False, trust_env=False,  # never via a proxy setting
            limits=httpx.Limits(max_keepalive_connections=0),  # one host name per connection
        )  # fmt: skip
        self._resolver = resolver
        self._sleep = sleep
        self._now = now
        self._tasks: set[asyncio.Task] = set()
        self._direct = 0  # "test" sends under way
        self._open = asyncio.Semaphore(MAX_IN_FLIGHT)
        self.sent = 0
        self.failed = 0

    def send(self, url: str, secret: str, payload: dict[str, Any],
             done: Callable[[str], None]) -> None:  # fmt: skip
        """Queue one send; `done("")` when it got through, `done(reason)` when it didn't."""
        if self.pending >= MAX_PENDING:
            done("too many webhook sends waiting: this one was dropped")
            return
        task = asyncio.ensure_future(self._deliver(url, secret, payload, done))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver_now(self, url: str, secret: str, payload: dict[str, Any]) -> str:
        """Send and wait for the result ("" when it got through): the "test" button."""
        if self.pending >= MAX_PENDING:
            return "too many webhook sends waiting: try again shortly"
        result: list[str] = []
        self._direct += 1
        try:
            await self._deliver(url, secret, payload, result.append)
        finally:
            self._direct -= 1
        return result[0]

    @property
    def pending(self) -> int:
        return len(self._tasks) + self._direct

    async def _deliver(self, url: str, secret: str, payload: dict[str, Any],
                       done: Callable[[str], None]) -> None:  # fmt: skip
        error = said = ""  # said: what the log gets (never the URL: it may be the secret)
        try:
            check_url(url)
            body = json.dumps(payload, separators=(",", ":"), default=str).encode()
            for attempt in range(len(BACKOFF_S) + 1):
                address = await self._checked_address(urlparse(url).hostname or "")
                error = await self._post(url, secret, body, address)
                if error is None:
                    error = ""
                    break
                if error.startswith("refused") or attempt == len(BACKOFF_S):
                    break
                await self._sleep(BACKOFF_S[attempt])
        except WebhookError as exc:
            error = said = str(exc)
        except Exception as exc:  # never let a webhook break anything else
            log.warning("webhook send failed: %s", type(exc).__name__)  # its URL may be secret
            error = f"{type(exc).__name__}: {exc}"  # to the user only (their own webhook)
            said = type(exc).__name__
        if error:
            self.failed += 1
            log.warning("webhook %s: %s", urlparse(url).hostname, said or error)
        else:
            self.sent += 1
        try:
            done(error)
        except Exception:
            log.exception("webhook result listener failed")

    async def _checked_address(self, host: str) -> str:
        """The address to connect to: looked up now, and only if every answer is public."""
        try:
            addresses = await self._resolver(host)
        except OSError as exc:
            raise WebhookError(f"the webhook host can't be found ({exc})") from exc
        if not addresses:
            raise WebhookError("the webhook host can't be found")
        try:
            if not all(public(a) for a in addresses):
                raise WebhookError("the webhook host is not a public address")
        except ValueError:
            raise WebhookError("the webhook host gave an address that isn't one") from None
        return addresses[0]

    async def _post(self, url: str, secret: str, body: bytes, address: str) -> str | None:
        """None when it got through; else why (a "refused …" reason is not tried again)."""
        ts = int(self._now())
        target, host, tls = pinned(url, address)
        headers = {"content-type": "application/json", "x-mocktrade-timestamp": str(ts),
                   "x-mocktrade-signature": sign(secret, ts, body), **host}  # fmt: skip
        try:
            async with self._open:
                r = await self._client.post(target, content=body, headers=headers,
                                            extensions=tls)  # fmt: skip
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
