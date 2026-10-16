"""Market alerts for the server: the engine, kept per user, told to the screens, the audit
trail and (when the user asked for it) their webhook.

    create / change / delete (REST) ─► AlertEngine ─► AlertStore (JSON per user)
    a tick fires one ─► audit ALERT_FIRED ─► the user's screens (stream "market_alert")
                                          ─► their webhook, signed, if the alert says so
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mocktrade.alerts.engine import Alert, AlertEngine, AlertError, Fired, alert_to_dict
from mocktrade.alerts.store import AlertStore
from mocktrade.alerts.webhook import WebhookError, WebhookSender, check_url, new_secret

if TYPE_CHECKING:
    from mocktrade.platform import Platform

Audit = Callable[[str, str, dict[str, Any]], None]


class MarketAlerts:
    def __init__(
        self,
        engine: AlertEngine,
        store: AlertStore,
        sender: WebhookSender | None = None,
        *,
        audit: Audit | None = None,
    ) -> None:
        self.engine = engine
        self.store = store
        self.sender = sender
        self._audit = audit or (lambda kind, detail, extra: None)
        self._fired: list[Callable[[Fired], None]] = []
        self._changed: list[Callable[[Alert], None]] = []
        engine.on_change(self._on_change)
        engine.on_fire(self._on_fire)

    def start(self) -> int:
        """Alerts kept from before a restart. Returns how many."""
        kept = self.store.load_all()
        self.engine.load(kept)
        return len(kept)

    def on_fire(self, listener: Callable[[Fired], None]) -> None:
        self._fired.append(listener)

    def on_change(self, listener: Callable[[Alert], None]) -> None:
        self._changed.append(listener)

    # --- the user's alerts --------------------------------------------------------------------

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return [alert_to_dict(a) for a in self.engine.of(user_id)]

    def create(self, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        a = self.engine.create(user_id, body)
        self._audit("ALERT_CREATED", f"{user_id} {a.alert_id}: {self._describe(a)}",
                    {"alert_id": a.alert_id})  # fmt: skip
        return alert_to_dict(a)

    def update(self, user_id: str, alert_id: str, body: dict[str, Any]) -> dict[str, Any]:
        a = self.engine.update(user_id, alert_id, body)
        self._audit("ALERT_CHANGED", f"{user_id} {a.alert_id}: {self._describe(a)}",
                    {"alert_id": a.alert_id})  # fmt: skip
        return alert_to_dict(a)

    def delete(self, user_id: str, alert_id: str) -> None:
        a = self.engine.delete(user_id, alert_id)
        self.store.changed(self.engine.of(user_id), user_id)
        self._audit("ALERT_DELETED", f"{user_id} {a.alert_id}", {"alert_id": a.alert_id})

    # --- the user's webhook --------------------------------------------------------------------

    def webhook(self, user_id: str) -> dict[str, Any]:
        hook = self.store.webhook(user_id)
        return {"url": hook["url"] if hook else None, "set": hook is not None}

    def set_webhook(self, user_id: str, url: str) -> dict[str, Any]:
        """A new address and a new secret: the secret is shown this once."""
        try:
            clean = check_url(url)
        except WebhookError as exc:
            raise AlertError(str(exc)) from None
        secret = new_secret()
        self.store.set_webhook(user_id, {"url": clean, "secret": secret})
        self._audit("ALERT_WEBHOOK_SET", f"{user_id} → {clean}", {})
        return {"url": clean, "set": True, "secret": secret}

    def clear_webhook(self, user_id: str) -> None:
        self.store.set_webhook(user_id, None)
        self._audit("ALERT_WEBHOOK_CLEARED", user_id, {})

    async def test_webhook(self, user_id: str) -> str:
        """Send a test message now; "" when it got through, else why."""
        hook = self.store.webhook(user_id)
        if hook is None:
            raise AlertError("no webhook is set")
        if self.sender is None:
            raise AlertError("this server doesn't send webhooks")
        payload = {"type": "test", "text": "MockTrade alerts: test message", "user": user_id}
        return await self.sender.deliver_now(hook["url"], hook["secret"], payload)

    # --- inside ----------------------------------------------------------------------------------

    def _describe(self, a: Alert) -> str:
        params = ", ".join(f"{k} {v}" for k, v in a.params.items())
        return f"{a.kind} {a.instrument_id} ({params}) {a.repeat}"

    def _on_change(self, a: Alert) -> None:
        self.store.changed(self.engine.of(a.user_id), a.user_id)
        for listener in list(self._changed):
            listener(a)

    def _on_fire(self, f: Fired) -> None:
        a = f.alert
        self._audit("ALERT_FIRED", f"{a.user_id} {a.alert_id}: {f.text}", {"alert_id": a.alert_id})
        for listener in list(self._fired):
            listener(f)
        hook = self.store.webhook(a.user_id) if a.webhook else None
        if hook is not None and self.sender is not None:
            payload = {"type": "market_alert", "alert": alert_to_dict(a), "text": f.text,
                       "at": f.at.isoformat(), "value": f.value}  # fmt: skip

            def told(error: str, alert_id: str = a.alert_id) -> None:
                self.engine.webhook_result(alert_id, error)

            self.sender.send(hook["url"], hook["secret"], payload, told)
        elif a.webhook:
            self.engine.webhook_result(a.alert_id, "no webhook is set")


def build_market_alerts(
    platform: "Platform", folder: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> MarketAlerts:
    """The server's alerts: checked on every tick, kept per user in `folder`, audited."""
    store = platform.instruments

    def name(instrument_id: str) -> str:
        found = store.get(instrument_id)
        return found.symbol if found else instrument_id

    engine = AlertEngine(platform.market_data.latest, lambda i: store.get(i) is not None,
                         clock=clock, name=name)  # fmt: skip
    events = platform.event_log
    audit: Audit | None = None
    if events is not None:

        def audit(kind: str, detail: str, extra: dict[str, Any]) -> None:
            events.record_note(kind, "-", detail, extra)

    service = MarketAlerts(engine, AlertStore(folder), WebhookSender(), audit=audit)
    service.start()
    platform.market_data.tap(engine.on_tick)
    return service
