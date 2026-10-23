"""Send alerts off the machine: a JSON POST to a webhook (Slack, Google Chat, ntfy, or any
service that relays to a phone). Configure MOCKTRADE_ALERT_WEBHOOK; leave it empty for none.

Only WARN and ERROR alerts, and their all-clears, are sent. Sending never blocks or breaks
trading: it runs in the background with a short timeout, and failures are only logged.
"""

import asyncio
import logging

import httpx

from mocktrade.ops.alerts import Alert

log = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(
        self, url: str, *, name: str = "MockTrade", client: httpx.AsyncClient | None = None
    ):
        self._url = url
        self._name = name
        self._client = client or httpx.AsyncClient(timeout=5)
        self._sent: set[str] = set()  # keys told to the phone (so their all-clear is sent too)
        self._tasks: set[asyncio.Task] = set()

    def __call__(self, alert: Alert) -> None:
        if alert.active and alert.level not in ("WARN", "ERROR"):
            return
        if not alert.active and alert.key not in self._sent:
            return
        if alert.active:
            self._sent.add(alert.key)
        else:
            self._sent.discard(alert.key)
        icon = {"ERROR": "🔴", "WARN": "🟠", "OK": "🟢"}.get(alert.level, "")
        text = f"{icon} {self._name}: {alert.text}"
        task = asyncio.ensure_future(
            self._post({"text": text, "level": alert.level, "key": alert.key})
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post(self, body: dict) -> None:
        try:
            response = await self._client.post(self._url, json=body)
            if response.status_code >= 300:
                log.warning("alert webhook answered %s", response.status_code)
        except httpx.HTTPError as exc:
            log.warning("alert webhook failed: %s", type(exc).__name__)  # no URL in logs (#367)

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()
