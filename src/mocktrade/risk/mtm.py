"""MTM watch: loss limits per position and for the whole account, checked on every price.

    position or price changes ─► MtmWatch.check
        each position with a rule: P&L today ≤ −limit (rupees, or % of what it's worth)?
        the account: day P&L ≤ −stop, or ≥ +target?
    reached ─► once a day per rule: an alert (stream "mtm", Messages, audit MTM_BREACHED)
            ─► if the trader turned on auto-close: closing orders through the normal path
               (risk/closing.py), audited MTM_CLOSE; a portfolio stop closes every position

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
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.money import format_rupees
from mocktrade.domain.orders import Product
from mocktrade.positions.book import PositionBook, PositionView
from mocktrade.recovery import IST
from mocktrade.risk.closing import close_position

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
    ) -> None:
        self.account_id = account_id
        self._book = book
        self._orders = orders
        self._instrument = instruments
        self._max_lots = max_lots
        self._path = path
        self._audit = audit
        self._clock = clock
        self.positions: dict[str, PositionRule] = {}
        self.portfolio: PortfolioRule | None = None
        self._fired: dict[str, date] = {}  # rule key -> the day it fired
        self._listeners: list[Callable[[MtmEvent], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._load()

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
        }

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
        if closing:
            basket = f"MTM{self._clock().strftime('%H%M%S')}"
            task = asyncio.get_running_loop().create_task(self._close(views, basket, text))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return event

    async def _close(self, views: list[PositionView], basket: str, why: str) -> None:
        for v in views:
            instrument = self._instrument(v.instrument_id)
            if instrument is None or not v.qty:
                continue
            try:
                sent = await close_position(self._orders, instrument, v.product, v.qty,
                                            user_id="mtm", max_lots=self._max_lots,
                                            basket_id=basket)  # fmt: skip
            except Exception:
                log.exception("MTM close of %s failed", v.instrument_id)
                continue
            if self._audit is not None:
                ids = [o.client_order_id for o in sent]
                what = f"closing {v.qty:+d} {v.instrument_id} {v.product.value}"
                self._audit("MTM_CLOSE", f"{what}: {', '.join(ids)} ({why})", {"orders": ids})

    def _changed(self, detail: str) -> None:
        if self._audit is not None:
            self._audit("MTM_RULE", detail, {})
        self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        data = {"positions": [asdict(r) for r in self.positions.values()],
                "portfolio": asdict(self.portfolio) if self.portfolio else None}  # fmt: skip
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self._path)

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for r in data.get("positions", []):
                rule = PositionRule(**r)
                self.positions[rule.key] = rule
            if data.get("portfolio"):
                self.portfolio = PortfolioRule(**data["portfolio"])
        except (ValueError, TypeError) as exc:
            log.error("MTM rules in %s can't be read (%s): none loaded", self._path, exc)


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
