"""MTM watch: loss limits per position and for the whole account, checked on every price.

    position or price changes ─► MtmWatch.check
        each position with a rule: P&L today ≤ −limit (rupees, or % of what it's worth)?
        the account: day P&L ≤ −stop, or ≥ +target?
    reached ─► once a day per rule: an alert (stream "mtm", Messages, audit MTM_BREACHED)
            ─► if the trader turned on auto-close: closing orders through the normal path
               (risk/closing.py), audited MTM_CLOSE; a portfolio stop closes every position

Every rule that fires leaves a breach record (#263) that lives through failures and restarts:

    ALERTED    alert only (auto-close off)
    REQUESTED  closing asked, the exit desk at work
    WORKING    closing orders working, the position not yet closed
    BLOCKED    nothing working and the position still open: a close was refused, failed, or
               cut short by a restart. It stays on screen with Retry; never retried by itself
    UNKNOWN    an order's fate or the position's size isn't known: wait for the broker, or
               check it there. Never retried while unknown
    COMPLETED  the position is closed, and the broker agrees on its size

    The state is worked out from the orders (by the close's basket ids) and the positions, so
    after a restart it is whatever the restored orders and positions say; nothing is sent
    again by itself. Which rules fired today is saved too, so a restart never fires one twice.

Rules and breaches are saved atomically (write, flush, rename). A rules file that can't be
read, or a rule in it that isn't valid, is never silently dropped: the file is kept aside,
`problem` says what is unprotected, and the screens show it in red.

The daily loss limit (risk/loss_watch.py) stops all trading at its limit; this is the
trader's own, earlier and finer stop. Rules are kept in a JSON file per account so a restart
doesn't silently drop a stop; each change is audited (MTM_RULE).

P&L is the position's P&L today (realised + unrealised, as on the Positions screen; carried
positions from the previous close). The percent rule compares the loss with the position's
value at its average price.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.money import format_rupees
from mocktrade.domain.orders import Product
from mocktrade.orders.model import Pending
from mocktrade.positions.book import PositionBook, PositionView
from mocktrade.recovery import IST
from mocktrade.risk.closing import ExitDesk, position_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionRule:
    instrument_id: str
    product: str
    max_loss_paise: int | None = None  # a loss of this many rupees today...
    max_loss_pct: float | None = None  # ...or this percent of the position's value
    auto_close: bool = False  # close it when reached (otherwise an alert only)

    @property
    def key(self) -> str:
        return f"{self.instrument_id}|{self.product}"


@dataclass(frozen=True)
class PortfolioRule:
    max_loss_paise: int | None = None  # the account's day P&L at or below −this
    target_paise: int | None = None  # ...or at or above +this (take the day's profit)
    auto_close: bool = False


@dataclass(frozen=True)
class MtmEvent:
    account_id: str
    kind: str  # "position" | "portfolio"
    key: str  # position key, or "portfolio"
    text: str  # what happened, in words
    closing: bool  # closing orders were sent


ALERTED, REQUESTED, WORKING = "ALERTED", "REQUESTED", "WORKING"
BLOCKED, UNKNOWN, COMPLETED = "BLOCKED", "UNKNOWN", "COMPLETED"
NEEDS_A_PERSON = (BLOCKED, UNKNOWN)


@dataclass
class Breach:
    """One rule firing on one day, and what became of its close (#263)."""

    id: str  # "<day>|<rule key>"
    day: str
    key: str  # the rule's key, or "portfolio"
    kind: str  # "position" | "portfolio"
    text: str  # why it fired, in words
    state: str
    targets: list[list[str]]  # [instrument_id, product] of each position it closes
    baskets: list[str] = field(default_factory=list)  # one per close attempt
    detail: str = ""  # what is left, or why it is blocked or unknown
    attempts: int = 0
    at: str = ""
    updated_at: str = ""


def rule_problems(rule: PositionRule | PortfolioRule) -> list[str]:
    problems = []
    if isinstance(rule, PositionRule):
        if rule.max_loss_paise is None and rule.max_loss_pct is None:
            problems.append("give a loss limit in rupees or percent")
        if rule.max_loss_paise is not None and rule.max_loss_paise <= 0:
            problems.append("the loss limit must be above 0")
        if rule.max_loss_pct is not None and not 0 < rule.max_loss_pct <= 100:
            problems.append("the percent must be between 0 and 100")
    else:
        if rule.max_loss_paise is None and rule.target_paise is None:
            problems.append("give a loss stop or a profit target")
        for value in (rule.max_loss_paise, rule.target_paise):
            if value is not None and value <= 0:
                problems.append("stops and targets must be above 0")
    return problems


class MtmWatch:
    def __init__(
        self,
        account_id: str,
        book: PositionBook,
        orders,  # OrderManager
        instruments: Callable[[str], Instrument | None],
        *,
        max_lots: int,
        path: Path | None = None,  # where the rules are kept (None: memory only)
        audit: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        desk: ExitDesk | None = None,  # the account's exit desk (#261)
        unknown: Callable[[str, Product], str | None] = lambda i, p: None,  # size not known
        ready: Callable[[], bool] = lambda: True,  # orders and positions restored (restart)
    ) -> None:
        self.account_id = account_id
        self._book = book
        self._orders = orders
        self._desk = desk or ExitDesk(orders, position_of(book))
        self._unknown = unknown
        self._ready = ready
        self._instrument = instruments
        self.max_lots = max_lots  # closing pieces; an admin's edit replaces it
        self._path = path
        self._audit = audit
        self._clock = clock
        self.positions: dict[str, PositionRule] = {}
        self.portfolio: PortfolioRule | None = None
        self._fired: dict[str, date] = {}  # rule key -> the day it fired (saved, #263)
        self._listeners: list[Callable[[MtmEvent], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self.breaches: dict[str, Breach] = {}  # by id
        self._closing: set[str] = set()  # breach ids the desk is working on now
        self._index: dict[str, list[str]] | None = None  # close basket id -> its order ids
        self.problem = ""  # what is unprotected because saved data couldn't be read
        self._load()
        self._load_state()

    # --- rules ------------------------------------------------------------------------------

    def set_position_rule(self, rule: PositionRule, user_id: str) -> None:
        problems = rule_problems(rule)
        if problems:
            raise ValueError("; ".join(problems))
        self.positions[rule.key] = rule
        self._fired.pop(rule.key, None)  # a new limit is watched afresh
        self._changed(f"{rule.key}: {_words(rule)} (by {user_id})")

    def remove_position_rule(self, key: str, user_id: str) -> None:
        if self.positions.pop(key, None) is not None:
            self._changed(f"{key}: no limit (by {user_id})")

    def set_portfolio_rule(self, rule: PortfolioRule | None, user_id: str) -> None:
        if rule is not None and (problems := rule_problems(rule)):
            raise ValueError("; ".join(problems))
        self.portfolio = rule
        self._fired.pop("portfolio", None)
        self._changed(f"portfolio: {_words(rule) if rule else 'no stop'} (by {user_id})")

    def subscribe(self, listener: Callable[[MtmEvent], None]) -> None:
        self._listeners.append(listener)

    # --- watching ---------------------------------------------------------------------------

    def check(self, *_: object) -> list[MtmEvent]:
        """Position book listener (accepts and ignores its argument). Cheap: dictionary looks."""
        if not self._ready():
            return []  # after a restart: not before orders and positions are back
        self.follow()
        today = self._clock().astimezone(IST).date()
        events = []
        for rule in list(self.positions.values()):
            if self._fired.get(rule.key) == today:
                continue
            view = self._book.get(rule.instrument_id, Product(rule.product))
            if view is None or (view.qty == 0 and view.pnl_paise >= 0):
                continue
            why = _breach(rule, view)
            if why:
                self._fired[rule.key] = today
                events.append(self._fire("position", rule.key, f"{rule.key}: {why}",
                                         rule.auto_close, [view]))  # fmt: skip
        rule = self.portfolio
        if rule is not None and self._fired.get("portfolio") != today:
            pnl = self._book.day_pnl_paise()
            why = None
            if rule.max_loss_paise is not None and pnl <= -rule.max_loss_paise:
                stop = format_rupees(-rule.max_loss_paise)
                why = f"day P&L {format_rupees(pnl)} reached the stop {stop}"
            elif rule.target_paise is not None and pnl >= rule.target_paise:
                target = format_rupees(rule.target_paise)
                why = f"day P&L {format_rupees(pnl)} reached the target {target}"
            if why:
                self._fired["portfolio"] = today
                open_ = [v for v in self._book.positions() if v.qty]
                events.append(self._fire("portfolio", "portfolio", f"portfolio: {why}",
                                         rule.auto_close, open_))  # fmt: skip
        return events

    async def settle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def to_dict(self) -> dict[str, Any]:
        today = self._clock().astimezone(IST).date()
        return {
            "positions": [
                {**asdict(r), "key": r.key, "fired_today": self._fired.get(r.key) == today}
                for r in self.positions.values()
            ],  # fmt: skip
            "portfolio": (
                {**asdict(self.portfolio), "fired_today": self._fired.get("portfolio") == today}
                if self.portfolio
                else None
            ),  # fmt: skip
            "breaches": [asdict(b) for b in self._shown()],
            "problem": self.problem,
        }

    # --- breaches (#263) ------------------------------------------------------------------------

    def on_order_event(self, event: object) -> None:
        """Order manager listener: a close's order changed (a refusal moves no position)."""
        order = getattr(event, "order", None)
        basket = getattr(getattr(order, "request", None), "basket_id", None)
        if not basket or not basket.startswith("MTM"):
            return
        if self._index is not None and basket in self._index:
            if order.client_order_id not in self._index[basket]:
                self._index[basket].append(order.client_order_id)
            self.follow()

    def _views(self, b: Breach) -> list:
        """The orders of a breach's closes, by basket id (an index, built once after a start
        from the restored orders, then kept up to date by order events)."""
        if self._index is None:
            self._index = {basket: [] for x in self.breaches.values() for basket in x.baskets}
            for o in self._orders.orders():
                if o.request.basket_id in self._index:
                    self._index[o.request.basket_id].append(o.client_order_id)
        return [v for basket in b.baskets for cid in self._index.get(basket, [])
                if (v := self._orders.get(cid)) is not None]  # fmt: skip

    def follow(self) -> None:
        """Work out each open breach's state from its orders and positions; save changes."""
        if not self._ready():
            return
        changed = False
        for b in self.breaches.values():
            if b.state in (ALERTED, COMPLETED):
                continue
            state, detail = self._state_of(b)
            if (state, detail) != (b.state, b.detail):
                b.state, b.detail, b.updated_at = state, detail, self._clock().isoformat()
                changed = True
                if self._audit is not None and state in (BLOCKED, UNKNOWN, COMPLETED):
                    self._audit(f"MTM_CLOSE_{state}", f"{b.key}: {detail}", {"breach": b.id})
        if changed:
            self._save_state()

    def needs_a_person(self) -> list[Breach]:
        return [b for b in self.breaches.values() if b.state in NEEDS_A_PERSON]

    async def retry(self, key: str, user_id: str) -> Breach:
        """Close a BLOCKED breach's positions again (a person asked). Only when nothing of it
        is working or unknown: never on top of a close that may still be happening."""
        self.follow()
        b = next((b for b in self.breaches.values() if b.key == key and b.state == BLOCKED),
                 None)  # fmt: skip
        if b is None:
            raise ValueError(f"no blocked close for {key}: only a blocked close can be retried")
        await self._close_breach(b, f"retry by {user_id}: {b.text}")
        return b

    def _state_of(self, b: Breach) -> tuple[str, str]:
        views = self._views(b)
        unconfirmed = [o.client_order_id for o in views if o.pending is Pending.UNCONFIRMED]
        if unconfirmed:
            return UNKNOWN, (f"the broker hasn't answered {', '.join(unconfirmed)}: wait for "
                             "it, or check the broker's own order book")  # fmt: skip
        left, unknown = [], []
        for instrument_id, product in b.targets:
            view = self._book.get(instrument_id, Product(product))
            qty = view.qty if view is not None else 0
            why = self._unknown(instrument_id, Product(product))
            if why:
                unknown.append(why)
            if qty:
                left.append(f"{instrument_id} {product} {qty:+d}")
        if unknown:
            return UNKNOWN, f"the position's size isn't known ({'; '.join(unknown)})"
        working = [o.client_order_id for o in views if not o.status.is_final]
        if not left:
            if working:
                return WORKING, f"closed; {', '.join(working)} still working"
            return COMPLETED, "closed: nothing left open"
        if b.id in self._closing:
            return REQUESTED, f"closing: {', '.join(left)} open"
        if working:
            return WORKING, f"{', '.join(left)} open; {', '.join(working)} working"
        refused = [f"{o.client_order_id}: {o.reason}" for o in views
                   if o.status.value == "REJECTED"]  # fmt: skip
        if refused:
            why = f"refused ({'; '.join(refused)})"
        elif not views:
            why = "nothing was sent (a failure, or a restart while closing)"
        else:
            why = "its orders ended without closing it"
        return BLOCKED, f"{', '.join(left)} still open: {why}; retry when ready"

    def _shown(self) -> list[Breach]:
        today = self._clock().astimezone(IST).date().isoformat()
        return [b for b in self.breaches.values() if b.day == today or b.state != COMPLETED]

    async def _close_breach(self, b: Breach, why: str) -> None:
        b.attempts += 1
        basket = f"MTM{self._clock().strftime('%H%M%S')}{b.attempts:02d}"
        b.baskets.append(basket)
        if self._index is not None:
            self._index.setdefault(basket, [])
        b.state, b.detail = REQUESTED, "closing"
        self._closing.add(b.id)
        self._save_state()  # before anything is sent: a restart now finds REQUESTED, not nothing
        try:
            await self._close([(i, Product(p)) for i, p in b.targets], basket, why)
        finally:
            self._closing.discard(b.id)
            self.follow()

    # --- inside -------------------------------------------------------------------------------

    def _fire(self, kind: str, key: str, text: str, close: bool,
              views: list[PositionView]) -> MtmEvent:  # fmt: skip
        closing = close and any(v.qty for v in views)
        text += "; closing" if closing else "; alert only (auto-close is off)"
        event = MtmEvent(self.account_id, kind, key, text, closing)
        log.warning("MTM %s: %s", self.account_id, text)
        if self._audit is not None:
            self._audit("MTM_BREACHED", text, {"key": key, "closing": closing})
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                log.exception("MTM listener failed")
        now = self._clock()
        day = now.astimezone(IST).date().isoformat()
        breach = Breach(f"{day}|{key}", day, key, kind, text, REQUESTED if closing else ALERTED,
                        [[v.instrument_id, v.product.value] for v in views if v.qty],
                        at=now.isoformat(), updated_at=now.isoformat())  # fmt: skip
        self.breaches[breach.id] = breach
        self._save_state()  # the firing is remembered before anything is sent
        if closing:
            task = asyncio.get_running_loop().create_task(self._close_breach(breach, text))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return event

    async def _close(self, targets: list[tuple[str, Product]], basket: str, why: str) -> None:
        for instrument_id, product in targets:
            instrument = self._instrument(instrument_id)
            if instrument is None:
                continue
            try:  # the desk reads the position afresh: it may have changed since the firing
                done = await self._desk.close(instrument, product, user_id="mtm",
                                              max_lots=self.max_lots, basket_id=basket,
                                              why=why)  # fmt: skip
            except Exception:  # the breach shows BLOCKED: nothing was sent
                log.exception("MTM close of %s failed", instrument_id)
                continue
            if self._audit is not None:
                self._audit("MTM_CLOSE", f"{done.words()} ({why})",
                            {"orders": done.ids, "left_open": done.left_open})  # fmt: skip

    def _changed(self, detail: str) -> None:
        if self._audit is not None:
            self._audit("MTM_RULE", detail, {})
        self._save()
        if self.problem.startswith("rules:"):
            self.problem = ""  # a person has set the limits again; the old file stays aside

    def _save(self) -> None:
        if self._path is None:
            return
        data = {"positions": [asdict(r) for r in self.positions.values()],
                "portfolio": asdict(self.portfolio) if self.portfolio else None}  # fmt: skip
        _write_atomically(self._path, data)

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            rules = data.get("positions", [])
            portfolio = data.get("portfolio")
            if not isinstance(rules, list) or not isinstance(portfolio, dict | None):
                raise ValueError("not a rules file")
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            kept = _keep_aside(self._path)
            self.problem = (f"rules: the saved MTM limits can't be read ({exc}); NO loss "
                            f"limits are watched for {self.account_id} until they are set "
                            f"again (the file is kept as {kept.name})")  # fmt: skip
            log.error("MTM %s", self.problem)
            return
        bad = []
        for r in rules:
            try:
                rule = PositionRule(**r)
                if problems := rule_problems(rule):
                    raise ValueError("; ".join(problems))
                self.positions[rule.key] = rule
            except (TypeError, ValueError) as exc:
                bad.append(f"{r.get('instrument_id', '?') if isinstance(r, dict) else r}: {exc}")
        if portfolio:
            try:
                rule = PortfolioRule(**portfolio)
                if problems := rule_problems(rule):
                    raise ValueError("; ".join(problems))
                self.portfolio = rule
            except (TypeError, ValueError) as exc:
                bad.append(f"portfolio: {exc}")
        if bad:
            kept = _keep_aside(self._path, copy=True)
            self.problem = (f"rules: {len(bad)} saved MTM limit(s) can't be used and are NOT "
                            f"watched ({'; '.join(bad)}); set them again (the file is kept "
                            f"as {kept.name})")  # fmt: skip
            log.error("MTM %s %s", self.account_id, self.problem)

    def _state_path(self) -> Path | None:
        return None if self._path is None else self._path.with_name(
            self._path.stem + "-state.json")  # fmt: skip

    def _save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        today = self._clock().astimezone(IST).date()
        data = {"fired": {k: d.isoformat() for k, d in self._fired.items() if d == today},
                "breaches": [asdict(b) for b in self._shown()]}  # fmt: skip
        _write_atomically(path, data)

    def _load_state(self) -> None:
        path = self._state_path()
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            fired = {k: date.fromisoformat(d) for k, d in data.get("fired", {}).items()}
            breaches = [Breach(**b) for b in data.get("breaches", [])]
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            kept = _keep_aside(path)
            # unsafe either way: without it a rule could fire twice today (a second close)
            self.problem = (f"state: what the MTM limits did today can't be read ({exc}); "
                            "auto-close is held for today so nothing closes twice; check "
                            f"positions and orders (the file is kept as {kept.name})")  # fmt: skip
            log.error("MTM %s %s", self.account_id, self.problem)
            today = self._clock().astimezone(IST).date()
            self._fired = {r: today for r in [*self.positions, "portfolio"]}
            return
        self._fired = fired
        self.breaches = {b.id: b for b in breaches}


def _write_atomically(path: Path, data: Any) -> None:
    """Write, flush to disk, then rename over the old file: a crash leaves old or new, whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _keep_aside(path: Path, *, copy: bool = False) -> Path:
    """Keep an unreadable file for a person to look at; never overwrite it."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
    if copy:
        kept.write_bytes(path.read_bytes())
    else:
        os.replace(path, kept)
    return kept


def _breach(rule: PositionRule, view: PositionView) -> str | None:
    pnl = view.pnl_paise
    if rule.max_loss_paise is not None and pnl <= -rule.max_loss_paise:
        return (
            f"P&L {format_rupees(pnl)} reached the loss limit {format_rupees(rule.max_loss_paise)}"
        )
    if rule.max_loss_pct is not None and view.qty:
        value = abs(view.qty) * view.avg_price_paise
        if value and -pnl * 100 >= rule.max_loss_pct * value:
            return (f"P&L {format_rupees(pnl)} is a {float(-pnl * 100 / value):.2f}% loss "
                    f"(limit {rule.max_loss_pct:g}%)")  # fmt: skip
    return None


def _words(rule: PositionRule | PortfolioRule | None) -> str:
    if rule is None:
        return "none"
    parts = []
    if rule.max_loss_paise is not None:
        parts.append(f"loss {format_rupees(rule.max_loss_paise)}")
    if isinstance(rule, PositionRule) and rule.max_loss_pct is not None:
        parts.append(f"loss {rule.max_loss_pct:g}%")
    if isinstance(rule, PortfolioRule) and rule.target_paise is not None:
        parts.append(f"target {format_rupees(rule.target_paise)}")
    parts.append("auto-close" if rule.auto_close else "alert only")
    return ", ".join(parts)
