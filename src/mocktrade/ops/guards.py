"""On every screen (the alert band): MTM limits reached, and MIS positions before the cut-off.

MtmWatch event ─► alert "mtm:<account>:<key>" (ERROR), raised at once
every minute    ─► clear it once its position (portfolio: every position) is flat
                ─► square-off window: alert "squareoff:<account>" (WARN) with the open MIS
                   positions and the minutes left; cleared when none are left or it's over
"""

from mocktrade.ops.alerts import Alerts
from mocktrade.risk.mtm import MtmEvent


class DayGuards:
    def __init__(self, platform, alerts: Alerts) -> None:
        self._platform = platform
        self._alerts = alerts
        self._mtm: dict[str, tuple[str, str]] = {}  # alert key -> (account, rule key)
        for runtime in platform.accounts.values():
            if runtime.mtm is not None:
                runtime.mtm.subscribe(self.on_mtm)

    def on_mtm(self, event: MtmEvent) -> None:
        key = f"mtm:{event.account_id}:{event.key}"
        self._mtm[key] = (event.account_id, event.key)
        self._alerts.raise_(key, "ERROR", f"{event.account_id} MTM {event.text}")

    async def check(self) -> None:
        """The scheduler's job, every minute."""
        for key, (account_id, rule_key) in list(self._mtm.items()):
            runtime = self._platform.account(account_id)
            if runtime is None:
                continue
            book = runtime.positions
            if rule_key == "portfolio":
                flat = not any(p.qty for p in book.positions())
            else:
                flat = not any(p.qty for p in book.positions()
                               if f"{p.instrument_id}|{p.product.value}" == rule_key)  # fmt: skip
            if flat:
                self._alerts.clear(key, f"{account_id} MTM {rule_key}: position closed")
                del self._mtm[key]
        for account_id, runtime in self._platform.accounts.items():
            if runtime.squareoff is None:
                continue
            text, key = runtime.squareoff.warning(), f"squareoff:{account_id}"
            if text:
                self._alerts.raise_(key, "WARN", text)
            else:
                self._alerts.clear(key, f"{account_id}: no MIS positions left")
