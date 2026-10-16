"""On every screen (the alert band): MTM limits reached, and MIS positions before the cut-off.

MtmWatch event ─► alert "mtm:<account>:<key>" (ERROR), raised at once
every minute    ─► clear it once its position (portfolio: every position) is flat
at start, then every minute all day (check_mtm, #263):
                ─► a close that needs a person (BLOCKED / UNKNOWN): alert
                   "mtm-close:<account>:<key>" (ERROR) with what is left and why
                ─► MTM data that couldn't be read: alert "mtm-data:<account>" (ERROR)
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
        self._closes: set[str] = set()  # MTM closes that need a person
        for runtime in platform.accounts.values():
            if runtime.mtm is not None:
                runtime.mtm.subscribe(self.on_mtm)
        self.check_mtm()  # unreadable limits are said at once, not at the next market open

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

    def check_mtm(self) -> None:
        """MTM closes that need a person, and MTM data that couldn't be read (any hour)."""
        for account_id, runtime in self._platform.accounts.items():
            if runtime.mtm is not None:
                self._mtm_attention(account_id, runtime.mtm)

    async def check_mtm_job(self) -> None:
        self.check_mtm()

    def _mtm_attention(self, account_id: str, mtm) -> None:
        mtm.follow()
        wanted = {f"mtm-close:{account_id}:{b.key}": b for b in mtm.needs_a_person()}
        for key, b in wanted.items():
            what = "close blocked" if b.state == "BLOCKED" else "close unclear"
            self._alerts.raise_(key, "ERROR", f"{account_id} MTM {b.key}: {what}: {b.detail}")
        for key in [k for k in self._closes if k.startswith(f"mtm-close:{account_id}:")]:
            if key not in wanted:
                self._alerts.clear(key, f"{account_id} MTM: {key.split(':', 2)[2]} settled")
                self._closes.discard(key)
        self._closes.update(wanted)
        key = f"mtm-data:{account_id}"
        if mtm.problem:
            self._alerts.raise_(key, "ERROR", f"{account_id} MTM {mtm.problem}")
        else:
            self._alerts.clear(key, f"{account_id} MTM limits readable again")
