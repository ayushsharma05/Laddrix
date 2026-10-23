"""Market alerts: price, % move, day volume, open interest and spread, checked on every tick.

    MarketData.tap ─► Alerts.on_tick ─► the alerts on that instrument (an index: O(those alerts))
                                         ─► crossed? ─► fired: listeners (the screens, the audit
                                            trail, the webhook sender; none of them on this loop
                                            for long)

An alert fires when its condition *becomes* true: it must first be seen false (armed), so an
alert made when the price is already above its level doesn't fire at once; it says so and fires
the next time the price crosses. A gap straight through the level counts (the check is on the
side of the level, not on touching it). "Every time" alerts arm again once the condition is
false again and the cool-down has passed; "once" alerts are done after firing.

The kinds are published as specs (the same format as the algo specs) so the screen draws its
form from the server and the server checks every value with the same description.
"""

import logging
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec, check_params, spec_to_dict
from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.ticks import Tick

log = logging.getLogger(__name__)

P = ParamKind
DIRECTION = ParamSpec("direction", "Direction", P.CHOICE, "Up, down or either way",
                      default="either", choices=("up", "down", "either"))  # fmt: skip
LEVEL = ParamSpec("level_paise", "Price", P.PRICE, "The level", min=1)
OTHER = ParamSpec("other", "Leg B", P.INSTRUMENT, "The contract subtracted")
SPREAD = ParamSpec("level_paise", "Spread (A − B)", P.PRICE_OFFSET, "The level, may be negative")


def _kind(code: str, name: str, help_: str, family: str, *params: ParamSpec) -> AlgoSpec:
    return AlgoSpec(code, name, help_, params, family=family)


KINDS: tuple[AlgoSpec, ...] = (
    _kind("PRICE_ABOVE", "Price crosses above",
          "When the last price rises to or through the level", "price", LEVEL),
    _kind("PRICE_BELOW", "Price crosses below",
          "When the last price falls to or through the level", "price", LEVEL),
    _kind("PCT_MOVE", "Moves from the previous close",
          "When the move from the previous close reaches the percent", "price",
          ParamSpec("pct", "Move", P.PERCENT, "Percent from the previous close", min=0.01,
                    max=100), DIRECTION),
    _kind("VOLUME_ABOVE", "Day volume above",
          "When the day's traded volume reaches the number", "activity",
          ParamSpec("volume", "Volume", P.COUNT, "Traded quantity today", min=1)),
    _kind("OI_CHANGE", "Open interest changes",
          "When open interest moves by the percent from when the alert armed", "activity",
          ParamSpec("pct", "Change", P.PERCENT, "Percent from the open interest when armed",
                    min=0.01, max=1000), DIRECTION),
    _kind("SPREAD_ABOVE", "Spread crosses above",
          "When this contract's price minus the other's rises to the level", "spread",
          OTHER, SPREAD),
    _kind("SPREAD_BELOW", "Spread crosses below",
          "When this contract's price minus the other's falls to the level", "spread",
          OTHER, SPREAD),
)  # fmt: skip
BY_CODE = {k.code: k for k in KINDS}
REPEATS = ("once", "every")
COOLDOWN = {"min": 10, "max": 86_400, "default": 300}  # seconds between two firings ("every")
MAX_ALERTS = 200  # per user
NOTE_MAX = 200


class AlertError(Exception):
    """A request that can't be done (said in words)."""


@dataclass
class Alert:
    alert_id: str
    user_id: str
    kind: str
    instrument_id: InstrumentId
    params: dict[str, Any]
    repeat: str = "once"
    cooldown_s: int = COOLDOWN["default"]
    note: str = ""
    webhook: bool = False
    enabled: bool = True
    created_at: str = ""
    armed: bool = False  # the condition has been seen false: a crossing can fire
    done: bool = False  # a "once" alert that fired
    fired_count: int = 0
    last_fired_at: str | None = None
    last_text: str = ""
    status: str = ""  # what the alert is waiting for, in words
    baseline_oi: int | None = None  # OI_CHANGE: the open interest when it armed
    webhook_error: str = ""
    webhook_ok_at: str | None = None

    def instruments(self) -> tuple[InstrumentId, ...]:
        other = self.params.get("other")
        return (self.instrument_id, other) if other else (self.instrument_id,)


def alert_to_dict(a: Alert) -> dict[str, Any]:
    d = asdict(a)
    d["active"] = a.enabled and not a.done
    return d


def alert_from_dict(d: dict[str, Any]) -> Alert:
    known = {f for f in Alert.__dataclass_fields__}
    return Alert(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class Fired:
    alert: Alert
    text: str
    at: datetime
    value: float | int


def spec() -> dict[str, Any]:
    return {
        "kinds": [spec_to_dict(k) for k in KINDS],
        "repeat": list(REPEATS),
        "cooldown_s": dict(COOLDOWN),
        "max_alerts": MAX_ALERTS,
        "note_max": NOTE_MAX,
    }


def _rupees(paise: float) -> str:
    sign = "−" if paise < 0 else ""
    return f"{sign}{abs(paise) / 100:,.2f}"


@dataclass
class _Eval:
    value: float | int  # what was measured
    true: bool  # the condition holds now
    text: str  # what happened, if it fires


class AlertEngine:
    def __init__(
        self,
        latest: Callable[[InstrumentId], Tick | None],
        known: Callable[[InstrumentId], bool],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        name: Callable[[InstrumentId], str] = lambda i: i.split(":", 1)[-1],
        watch: Callable[[set[InstrumentId]], None] = lambda ids: None,
        # a server with a price source per subscription (#395): whose prices each user's
        # alerts are checked on (None: no prices of their own, nothing fires)
        source_of: Callable[[str], Any] | None = None,
    ) -> None:
        self._latest = latest
        self._source_of = source_of
        self._known = known
        self._clock = clock
        self._name = name
        self._watch = watch  # told the instruments alerts need prices for (a feed streams them)
        self._alerts: dict[str, Alert] = {}
        self._index: dict[InstrumentId, set[str]] = {}
        self._listeners: list[Callable[[Fired], None]] = []
        self._changed: list[Callable[[Alert], None]] = []
        self.checked = 0

    # --- listeners --------------------------------------------------------------------------

    def on_fire(self, listener: Callable[[Fired], None]) -> None:
        self._listeners.append(listener)

    def on_change(self, listener: Callable[[Alert], None]) -> None:
        """Every change of an alert's state (to keep it saved)."""
        self._changed.append(listener)

    # --- the alerts a user keeps ---------------------------------------------------------------

    def of(self, user_id: str) -> list[Alert]:
        return sorted((a for a in self._alerts.values() if a.user_id == user_id),
                      key=lambda a: a.created_at)  # fmt: skip

    def get(self, user_id: str, alert_id: str) -> Alert:
        a = self._alerts.get(alert_id)
        if a is None or a.user_id != user_id:
            raise LookupError(f"no alert {alert_id}")  # another user's alerts are not found
        return a

    def load(self, alerts: list[Alert]) -> None:
        """Alerts kept from before a restart: watching again from their next tick."""
        for a in alerts:
            self._add(a)
            self._evaluate(a, None, fresh=True)

    def create(self, user_id: str, body: dict[str, Any]) -> Alert:
        if len(self.of(user_id)) >= MAX_ALERTS:
            raise AlertError(f"at most {MAX_ALERTS} alerts each")
        a = Alert(f"AL{secrets.token_hex(4).upper()}", user_id, "", "", {},
                  created_at=self._clock().isoformat())  # fmt: skip
        self._apply(a, body, creating=True)
        self._add(a)
        self._evaluate(a, None, fresh=True)
        self._tell_changed(a)
        return a

    def update(self, user_id: str, alert_id: str, body: dict[str, Any]) -> Alert:
        a = self.get(user_id, alert_id)
        before = a.instruments()
        trial = alert_from_dict(alert_to_dict(a))
        self._apply(trial, body, creating=False)  # check everything before changing anything
        self._unindex(alert_id, before)
        for k in ("kind", "instrument_id", "params", "repeat", "cooldown_s", "note", "webhook",
                  "enabled"):  # fmt: skip
            setattr(a, k, getattr(trial, k))
        if "enabled" in body and a.enabled:
            a.done = False  # switched on again: a fired "once" alert watches again
        self._index_alert(a)
        self._evaluate(a, None, fresh=True)
        self._tell_changed(a)
        return a

    def delete(self, user_id: str, alert_id: str) -> Alert:
        a = self.get(user_id, alert_id)
        self._unindex(alert_id, a.instruments())
        del self._alerts[alert_id]
        return a

    def webhook_result(self, alert_id: str, error: str) -> None:
        a = self._alerts.get(alert_id)
        if a is None:
            return
        a.webhook_error = error
        if not error:
            a.webhook_ok_at = self._clock().isoformat()
        self._tell_changed(a)

    # --- every tick ---------------------------------------------------------------------------

    def on_tick(self, tick: Tick, source: Any = None) -> None:
        """MarketData tap: only the alerts on this instrument are looked at, and (with
        `source`, #395) only those whose owner's own prices this tick is."""
        ids = self._index.get(tick.instrument_id)
        if not ids:
            return
        for alert_id in list(ids):
            a = self._alerts.get(alert_id)
            if (a is not None and source is not None and self._source_of is not None
                    and self._source_of(a.user_id) is not source):  # fmt: skip
                continue  # another subscription's price: never this user's alert's
            if a is not None and a.enabled and not a.done:
                self.checked += 1
                self._evaluate(a, tick)

    # --- inside ---------------------------------------------------------------------------------

    def _apply(self, a: Alert, body: dict[str, Any], *, creating: bool) -> None:
        problems = []
        kind = body.get("kind", a.kind)
        spec_ = BY_CODE.get(kind)
        if spec_ is None:
            raise AlertError(f"kind must be one of {', '.join(BY_CODE)}")
        instrument = body.get("instrument_id", a.instrument_id)
        if not isinstance(instrument, str) or not self._known(instrument):
            problems.append(f"unknown instrument {instrument}")
        raw = body.get("params", a.params if not creating else {})
        if not isinstance(raw, dict):
            raise AlertError("params must be an object")
        params, wrong = check_params(spec_, raw)
        problems += wrong
        other = params.get("other")
        if other is not None:
            if not self._known(other):
                problems.append(f"unknown instrument {other}")
            elif other == instrument:
                problems.append("Leg B must be another contract")
        repeat = body.get("repeat", a.repeat)
        if repeat not in REPEATS:
            problems.append("repeat must be once or every")
        cooldown = body.get("cooldown_s", a.cooldown_s)
        if (
            isinstance(cooldown, bool)
            or not isinstance(cooldown, int)
            or not (COOLDOWN["min"] <= cooldown <= COOLDOWN["max"])
        ):
            problems.append(f"cool-down must be {COOLDOWN['min']}–{COOLDOWN['max']} seconds")
        note = body.get("note", a.note)
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            problems.append(f"the note must be text of at most {NOTE_MAX} characters")
        webhook = body.get("webhook", a.webhook)
        enabled = body.get("enabled", a.enabled)
        if not isinstance(webhook, bool) or not isinstance(enabled, bool):
            problems.append("webhook and enabled are yes or no")
        if problems:
            raise AlertError("; ".join(problems))
        a.kind, a.instrument_id, a.params = kind, instrument, params
        a.repeat, a.cooldown_s, a.note = repeat, cooldown, note.strip()
        a.webhook, a.enabled = webhook, enabled

    def _add(self, a: Alert) -> None:
        self._alerts[a.alert_id] = a
        self._index_alert(a)

    def watched(self) -> set[InstrumentId]:
        """The instruments some alert needs prices of."""
        return set(self._index)

    def watched_for(self, source: Any) -> set[InstrumentId]:
        """The instruments the alerts checked on this source need (#395)."""
        return {i for a in self._alerts.values() if self._source_of(a.user_id) is source
                for i in a.instruments()}  # fmt: skip

    def _latest_of(self, a: "Alert", instrument_id: InstrumentId) -> Tick | None:
        """The latest price for this alert: from its owner's own prices (#395)."""
        if self._source_of is None:
            return self._latest(instrument_id)
        source = self._source_of(a.user_id)
        return source.latest(instrument_id) if source is not None else None

    def _index_alert(self, a: Alert) -> None:
        new = False
        for i in a.instruments():
            new |= i not in self._index
            self._index.setdefault(i, set()).add(a.alert_id)
        if new:
            self._watch(self.watched())

    def _unindex(self, alert_id: str, instruments: tuple[InstrumentId, ...]) -> None:
        gone = False
        for i in instruments:
            ids = self._index.get(i)
            if ids is not None:
                ids.discard(alert_id)
                if not ids:
                    del self._index[i]
                    gone = True
        if gone:
            self._watch(self.watched())

    def _measure(self, a: Alert, tick: Tick | None) -> _Eval | str:
        """The condition now, or what the alert is waiting for (in words)."""
        t = (
            tick
            if tick is not None and tick.instrument_id == a.instrument_id
            else self._latest_of(a, a.instrument_id)
        )
        if t is None:
            return "waiting for a price"
        name, p = self._name(a.instrument_id), a.params
        if a.kind in ("PRICE_ABOVE", "PRICE_BELOW"):
            level = p["level_paise"]
            up = a.kind == "PRICE_ABOVE"
            true = t.ltp_paise >= level if up else t.ltp_paise <= level
            text = f"{name} crossed {'above' if up else 'below'} {_rupees(level)}"
            return _Eval(t.ltp_paise, true, f"{text} (last {_rupees(t.ltp_paise)})")
        if a.kind == "PCT_MOVE":
            if not t.close_paise:
                return "waiting for the previous close"
            pct = (t.ltp_paise - t.close_paise) * 100 / t.close_paise
            return _Eval(round(pct, 4), _directed(pct, p["pct"], p["direction"]),
                         f"{name} moved {pct:+.2f}% from the previous close "
                         f"(last {_rupees(t.ltp_paise)})")  # fmt: skip
        if a.kind == "VOLUME_ABOVE":
            if t.volume is None:
                return "waiting for the day's volume"
            return _Eval(t.volume, t.volume >= p["volume"],
                         f"{name} day volume {t.volume:,} reached {p['volume']:,}")  # fmt: skip
        if a.kind == "OI_CHANGE":
            if t.open_interest is None:
                return "waiting for open interest"
            if a.baseline_oi is None or a.baseline_oi <= 0:
                return _Eval(0, False, "")  # the baseline is set when it arms
            pct = (t.open_interest - a.baseline_oi) * 100 / a.baseline_oi
            return _Eval(round(pct, 4), _directed(pct, p["pct"], p["direction"]),
                         f"{name} open interest {t.open_interest:,} is {pct:+.2f}% from "
                         f"{a.baseline_oi:,}")  # fmt: skip
        other_tick = (tick if tick is not None and tick.instrument_id == p["other"]
                      else self._latest_of(a, p["other"]))  # fmt: skip
        if other_tick is None:
            return f"waiting for a price of {self._name(p['other'])}"
        spread = t.ltp_paise - other_tick.ltp_paise
        up = a.kind == "SPREAD_ABOVE"
        level = p["level_paise"]
        return _Eval(spread, spread >= level if up else spread <= level,
                     f"{name} − {self._name(p['other'])} crossed {'above' if up else 'below'} "
                     f"{_rupees(level)} (now {_rupees(spread)})")  # fmt: skip

    def _evaluate(self, a: Alert, tick: Tick | None, *, fresh: bool = False) -> None:
        if fresh:
            a.armed = False
            a.baseline_oi = None
        if not a.enabled or a.done:
            a.status = "off" if not a.enabled else "fired"
            return
        e = self._measure(a, tick)
        before = (a.armed, a.status)
        if isinstance(e, str):
            a.status = e
        elif not e.true:
            if not a.armed:
                a.armed = True
                if a.kind == "OI_CHANGE":
                    t = self._latest_of(a, a.instrument_id) if tick is None else tick
                    a.baseline_oi = t.open_interest if t is not None else None
            a.status = "watching"
        elif not a.armed:
            if fresh or a.fired_count == 0:  # (after a firing, its status says what comes next)
                a.status = "already true: fires the next time it crosses"
        elif self._cooling(a):
            a.status = "cooling down"
        else:
            self._fire(a, e)
            return
        if (a.armed, a.status) != before:
            self._tell_changed(a)

    def _cooling(self, a: Alert) -> bool:
        if a.last_fired_at is None:
            return False
        since = self._clock() - datetime.fromisoformat(a.last_fired_at)
        return since < timedelta(seconds=a.cooldown_s)

    def _fire(self, a: Alert, e: _Eval) -> None:
        now = self._clock()
        a.armed = False
        a.baseline_oi = None
        a.fired_count += 1
        a.last_fired_at = now.isoformat()
        a.last_text = e.text + (f" · {a.note}" if a.note else "")
        if a.repeat == "once":
            a.done = True
            a.status = "fired"
        else:
            a.status = "fired: arms again once it is false again"
        fired = Fired(a, a.last_text, now, e.value)
        for listener in list(self._listeners):
            try:
                listener(fired)
            except Exception:
                log.exception("alert listener failed")
        self._tell_changed(a)

    def _tell_changed(self, a: Alert) -> None:
        for listener in list(self._changed):
            try:
                listener(a)
            except Exception:
                log.exception("alert change listener failed")


def _directed(pct: float, threshold: float, direction: str) -> bool:
    if direction == "up":
        return pct >= threshold
    if direction == "down":
        return pct <= -threshold
    return abs(pct) >= threshold
