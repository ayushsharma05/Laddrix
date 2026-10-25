"""What every part of the HTTP API shares: the server's settings, its services, and the checks
each request goes through (who is asking, which account, which firm).

    create_app ─► ServerContext(platform, users, settings, services…)
                     │
                     ├─► routes/ops.py, session.py, market.py, …   each: router(ctx)
                     └─► admin.py                                 admin_router(…)

A route never decides on its own who may see what: it asks the context (runtime(),
in_workspace(), admin_of(), platform_wide()), so the rules live in one place.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from fastapi import HTTPException, Request

from laddrix.alerts.service import MarketAlerts
from laddrix.domain.accounts import BrokerAccount, Role, User, can_manage, can_view
from laddrix.marketdata.markets import Market, Markets
from laddrix.marketdata.service import MarketData
from laddrix.ops import logs
from laddrix.ops.alerts import Alerts
from laddrix.ops.protection import ProtectionGuard
from laddrix.ops.scheduler import Scheduler
from laddrix.ops.status import Status
from laddrix.platform import AccountRuntime, Platform
from laddrix.reports.service import Reports
from laddrix.research.backtest import Backtests, CandleStore
from laddrix.research.replay import ReplayFeed
from laddrix.server.users import UserStore
from laddrix.spreads.store import SpreadBook

# what a user whose password an admin reset may still reach: who am I, change it, log out
PASSWORD_FIRST = frozenset({"/api/me", "/api/me/password", "/api/session"})


@dataclass(frozen=True)
class ServerSettings:
    origin: str  # where the screens are served from, e.g. "https://trade.example.in"
    cookie_secure: bool = True  # False only for local development over plain http
    # a monitoring system's own key to read GET /api/metrics?format=prometheus with no
    # login (#287): "Authorization: Bearer <it>". Empty: only signed-in admin and risk read
    metrics_token: str = field(default="", repr=False)

    @property
    def cookie_name(self) -> str:
        # "__Host-" makes browsers refuse the cookie unless it's Secure, Path=/, no Domain.
        return "__Host-mt_session" if self.cookie_secure else "mt_session"


class BrokerLogin(Protocol):
    """How a broker's own login is started and finished (Kite's daily login)."""

    def login_url(self, account: BrokerAccount, state: str) -> str: ...

    async def complete(self, account: BrokerAccount, request_token: str) -> None: ...


@dataclass
class ServerContext:
    """The server's parts, as create_app was given them, and the checks built on them."""

    platform: Platform
    users: UserStore
    settings: ServerSettings
    markets: Markets  # what the screens read from prices, one per subscription (#395)
    no_prices: MarketData  # someone with no subscription of their own: nothing ticks
    book: SpreadBook  # each user's spread definitions (#414)
    status: Status
    broker_login: BrokerLogin | None = None
    broker_status: Callable[[BrokerAccount], str | None] | None = None
    alerts: Alerts | None = None
    scheduler: Scheduler | None = None
    reports: Reports | None = None
    replay_command: Callable[[date], str] | None = None
    replay: ReplayFeed | None = None
    backtests: Backtests | None = None
    candles: CandleStore | None = None
    candles_for: Callable[[str], CandleStore] | None = None
    market_alerts: MarketAlerts | None = None
    protection: ProtectionGuard | None = None
    monotonic: Callable[[], float] = time.monotonic
    login_attempts: dict[str, deque[float]] = field(default_factory=dict)  # per address
    # a broker login under way: state -> (user, account, expiry)
    login_states: dict[str, tuple[str, str, float]] = field(default_factory=dict)

    # --- who is asking -----------------------------------------------------------------------

    def current_user(self, request: Request) -> User:
        user = self.users.session_user(request.cookies.get(self.settings.cookie_name))
        if user is None:
            raise HTTPException(401, "please log in")
        logs.note(user=user.id)  # this request's log lines say whose it is
        if user.must_change_password and request.url.path not in PASSWORD_FIRST:
            raise HTTPException(403, "set a new password first (an admin reset it)")
        return user

    def require_step_up(self, request: Request, what: str) -> None:
        """Privileged changes need a fresh authenticator code, not just the session: a stolen
        session alone can't loosen limits, add traders, reset others' logins or resume the
        firm. Never asked for STOP, Flatten or cancels (reducing risk is never blocked)."""
        if not self.users.stepped_up(request.cookies.get(self.settings.cookie_name)):
            raise HTTPException(428, f"step-up: enter a fresh authenticator code to {what}")

    def market(self, user: User) -> Market:
        """The prices the asker sees: their own subscription's (#395), never another's."""
        found = self.platform.prices.for_user(user, self.users.accounts())
        return self.markets.of(found if found is not None else self.no_prices)

    # --- workspaces (#368, docs/design/tenancy.md) -------------------------------------------

    def in_workspace(self, user: User, account_id: str) -> bool:
        """The account exists and is in the user's own workspace."""
        account = self.users.get_account(account_id)
        return account is not None and account.workspace == user.workspace

    def admin_of(self, user: User, owner: str) -> bool:
        """An admin sees the backtests of their own workspace's users (#270, #368)."""
        if not user.has_role(Role.ADMIN):
            return False
        found = self.users.get_user(owner) if owner else None
        return found is not None and found.workspace == user.workspace

    def platform_wide(self, user: User, what: str) -> None:
        """Changes that touch every firm on the server (resetting metrics, a replay's clock,
        the shared recordings and candles) are an admin's only while the server holds one
        firm. With several, only the operator makes them, from the server's terminal."""
        if len(self.users.workspaces()) > 1:
            raise HTTPException(403, f"with several firms on this server, only its operator "
                                f"can {what}, from the server's terminal")  # fmt: skip

    def runtime(self, account_id: str, user: User, *, trade: bool = False) -> AccountRuntime:
        account = self.users.get_account(account_id)
        running = self.platform.account(account_id)
        if account is None or running is None or not can_view(user, account):
            raise HTTPException(404, "no such account")  # don't reveal other people's accounts
        # a turned-off account still lets its traders cancel and close: the risk checks pass
        # only closing trades on it (AccountRuntime.set_active)
        if trade and not can_manage(user, account):
            raise HTTPException(403, "you can view this account but not trade it")
        return running

    def audit(self, kind: str, detail: str, extra: dict[str, Any], account_id: str = "-") -> None:
        if self.platform.event_log is not None:
            self.platform.event_log.record_note(kind, account_id, detail, extra)


def risk_or_admin(user: User) -> bool:
    return user.has_role(Role.RISK) or user.has_role(Role.ADMIN)


def report_day(text: str) -> date:
    """A day from the address, e.g. "2026-10-23"; anything else is a 422 in words."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise HTTPException(422, f"not a date: {text!r} (YYYY-MM-DD)") from None
