"""The HTTP API: login, accounts, orders, positions, kill switches, audit history.

Every /api route except login and health needs a session cookie. Rules that hold
for every request:
- Who you are comes from the session, never from the request body.
- Account routes check can_view / can_manage for that account (a turned-off account takes
  only closing trades: the risk checks say so).
- Changing requests (POST, PATCH, PUT, DELETE) must come from our own page: the
  Origin header must match, the cookie is SameSite=Strict, and bodies are JSON.
- Responses carry security headers (no framing, strict content policy, HSTS).

Money is whole paise in and out (the screens format it); order objects use the
same format as the event log (orders/codec.py).
"""

import asyncio
import csv
import io
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from mocktrade.alerts.engine import AlertError
from mocktrade.alerts.engine import spec as alert_spec
from mocktrade.alerts.service import MarketAlerts
from mocktrade.algos.autospreader import SPEC as ASE
from mocktrade.algos.engine import AlgoError, algo_id_note
from mocktrade.algos.spec import spec_to_dict as algo_spec_to_dict
from mocktrade.broker.base import BrokerError
from mocktrade.charges.model import bill_to_dict
from mocktrade.charges.schedules import schedule_to_dict
from mocktrade.charges.service import ChargesError
from mocktrade.charts.candles import IST
from mocktrade.charts.service import ChartError, Charts
from mocktrade.domain.accounts import BrokerAccount, Role, User, can_manage, can_view
from mocktrade.domain.gtt import gtt_spec_to_dict, gtt_to_dict
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.order_spec import spec_to_dict
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side, Validity
from mocktrade.domain.portfolio import trade_to_dict
from mocktrade.marketdata.markets import Market, Markets
from mocktrade.marketdata.movers import MoversError
from mocktrade.marketdata.prints import print_to_dict
from mocktrade.marketdata.service import MarketData
from mocktrade.metrics import METRICS, process_gauges, since_us
from mocktrade.ops.alerts import Alerts, alert_to_dict
from mocktrade.ops.protection import GUARANTEE, ProtectionGuard, RestoreError
from mocktrade.ops.scheduler import Scheduler
from mocktrade.options.strategy import Strategies, StrategyError
from mocktrade.options.strategy import templates as strategy_templates_list
from mocktrade.orders.basket import BasketError, LegIn, check_to_dict
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.orders.gtt import GttError, gtt_request_from
from mocktrade.orders.model import OrderError
from mocktrade.platform import AccountRuntime, Platform, spec_of
from mocktrade.portfolio.service import PortfolioError, snapshot_to_dict
from mocktrade.positions.external import ExternalError
from mocktrade.refdata.expiry import Expiries, expiry_to_dict
from mocktrade.reports.export import report_csv, report_pdf
from mocktrade.reports.service import ReportError, Reports
from mocktrade.research.backtest import (
    MAX_DAYS,
    BacktestBusy,
    BacktestError,
    Backtests,
    BacktestSpec,
    CandleStore,
    form_spec,
)
from mocktrade.research.replay import ReplayFeed
from mocktrade.research.ticks import FileTickStore
from mocktrade.risk.kill_switch import stop_trading
from mocktrade.risk.mtm import PortfolioRule, PositionRule
from mocktrade.risk.surveillance import alert_to_dict as surveillance_to_dict
from mocktrade.server import security
from mocktrade.server.admin import AccountStarter, admin_router
from mocktrade.server.formats import algo_to_dict, position_to_dict
from mocktrade.server.stream import StreamHub
from mocktrade.server.users import SESSION_MAX, LoginFailed, UserStore
from mocktrade.spreads.model import (
    PREFIX,
    SpreadDef,
    SpreadLeg,
    leg_to_dict,
    spread_tick,
    spread_to_dict,
)
from mocktrade.spreads.pricer import SpreadPricer
from mocktrade.spreads.store import SpreadBook, SpreadError
from mocktrade.store.events import StoredEvent
from mocktrade.version import BUILD, VERSION

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOGIN_LIMIT_PER_IP = 20  # attempts per 15 minutes from one address
LOGIN_WINDOW_S = 15 * 60
STATE_TTL_S = 10 * 60

POPOUT_CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
        "object-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}


@dataclass(frozen=True)
class ServerSettings:
    origin: str  # where the screens are served from, e.g. "https://trade.example.in"
    cookie_secure: bool = True  # False only for local development over plain http

    @property
    def cookie_name(self) -> str:
        # "__Host-" makes browsers refuse the cookie unless it's Secure, Path=/, no Domain.
        return "__Host-mt_session" if self.cookie_secure else "mt_session"


class BrokerLogin(Protocol):
    """How a broker's own login is started and finished (Kite's daily login)."""

    def login_url(self, account: BrokerAccount, state: str) -> str: ...

    async def complete(self, account: BrokerAccount, request_token: str) -> None: ...


# --- request bodies (unknown fields are refused: no smuggling a user or account id) ---------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# what a user whose password an admin reset may still reach: who am I, change it, log out
PASSWORD_FIRST = frozenset({"/api/me", "/api/me/password", "/api/session"})


class CodeIn(_Strict):
    code: str = Field(min_length=6, max_length=6)


class PasswordIn(_Strict):
    old: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class LoginIn(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=6)


class OrderIn(_Strict):
    client_order_id: str = Field(default="", pattern=r"^[A-Za-z0-9]{0,20}$")
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0)
    order_type: OrderType
    product: Product
    validity: Validity = Validity.DAY
    price_paise: int | None = Field(default=None, gt=0)
    trigger_paise: int | None = Field(default=None, gt=0)
    market_protection: Decimal | None = None


class ModifyIn(_Strict):
    qty: int | None = Field(default=None, gt=0)
    price_paise: int | None = Field(default=None, gt=0)
    trigger_paise: int | None = Field(default=None, gt=0)


class AlgoIn(_Strict):
    code: str = Field(min_length=1, max_length=32)
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0)
    product: Product = Product.NRML
    params: dict[str, Any] = Field(default_factory=dict)  # checked against the algo's spec


class StrategyBuildIn(_Strict):
    code: str = Field(min_length=1, max_length=40)
    underlying: str = Field(min_length=1, max_length=40)
    expiry: date
    width: int = Field(default=2, ge=1, le=20)
    lots: int = Field(default=1, ge=1, le=100)


class StrategyLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    lots: int = Field(ge=1, le=1000)
    price_paise: int | None = Field(default=None, gt=0)  # entry price; default: live mid


class StrategyIn(_Strict):
    legs: list[StrategyLegIn] = Field(min_length=1, max_length=8)


class BasketLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    lots: int = Field(ge=1, le=1000)
    product: Product = Product.NRML
    price_paise: int | None = Field(default=None, gt=0)
    at: Literal["touch", "mid"] = "touch"


class BasketIn(_Strict):
    legs: list[BasketLegIn] = Field(min_length=1, max_length=10)


class GttLegIn(_Strict):
    trigger_paise: int = Field(gt=0)
    side: Side
    qty: int = Field(gt=0)
    price_paise: int = Field(gt=0)


class GttIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    product: Product
    kind: str = Field(pattern=r"^(single|two-leg)$")
    legs: list[GttLegIn] = Field(min_length=1, max_length=2)


class ChargesIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0, le=10_000_000)
    price_paise: int = Field(gt=0, le=10**12)
    product: Product


class PositionRuleIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    product: Product
    max_loss_paise: int | None = Field(default=None, gt=0, le=10**13)
    max_loss_pct: float | None = Field(default=None, gt=0, le=100)
    auto_close: bool = False


class PortfolioRuleIn(_Strict):
    max_loss_paise: int | None = Field(default=None, gt=0, le=10**13)
    target_paise: int | None = Field(default=None, gt=0, le=10**13)
    auto_close: bool = False


class ReplayIn(_Strict):
    action: str = Field(pattern=r"^(pause|resume|speed|seek)$")
    speed: float | None = None
    at: datetime | None = None  # seek: the time to jump to (ahead only)


class BacktestIn(_Strict):
    days: list[str] = Field(min_length=1, max_length=MAX_DAYS)
    algo: str = Field(min_length=1, max_length=40)
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0, le=10_000_000)
    params: dict[str, Any] = Field(default_factory=dict)
    product: Product = Product.NRML
    start: str = "09:15"
    capital_paise: int = Field(default=10_00_000_00, gt=0, le=10**13)
    name: str = Field(default="", max_length=80)
    candles: bool = False


class AlertIn(_Strict):
    kind: str = Field(min_length=1, max_length=40)
    instrument_id: str = Field(min_length=3, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    repeat: str = "once"
    cooldown_s: int = 300
    note: str = Field(default="", max_length=200)
    webhook: bool = False


class AlertPatch(_Strict):
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    instrument_id: str | None = Field(default=None, min_length=3, max_length=64)
    params: dict[str, Any] | None = None
    repeat: str | None = None
    cooldown_s: int | None = None
    note: str | None = Field(default=None, max_length=200)
    webhook: bool | None = None
    enabled: bool | None = None


class WebhookIn(_Strict):
    url: str = Field(min_length=8, max_length=500)


class CandlesIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    day: date
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class ConfirmIn(_Strict):
    confirm: bool


class MtmRetryIn(_Strict):
    key: str = Field(min_length=1, max_length=100)  # the rule's key, or "portfolio"
    confirm: bool


class RestoreIn(_Strict):
    instrument_id: str = Field(min_length=1, max_length=60)
    confirm: bool


class FlattenIn(_Strict):
    confirm: bool
    instrument_id: str | None = None  # one instrument only (else every open position)
    product: Product | None = None  # with instrument_id: one product only


class AckIn(_Strict):
    note: str  # what was found: it is kept


class AdoptIn(_Strict):
    instrument_id: str
    product: str
    acknowledgement: str  # what the person checked: it goes in the audit trail


class StopIn(_Strict):
    reason: str = Field(min_length=1, max_length=200)


class SettingIn(_Strict):
    value: dict[str, Any] | None


class LayoutIn(_Strict):
    layout: dict[str, Any]  # the screens' own description of their widgets


class SpreadLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    ratio: int  # the rules (range, sign, one quoting leg) are checked in words: spreads/model.py
    multiplier: int
    quote: bool = False
    payup_ticks: int = 2


class SpreadIn(_Strict):
    name: str = Field(max_length=200)
    legs: list[SpreadLegIn] = Field(max_length=20)


class SpreadOrderIn(_Strict):
    side: Side  # of the spread: BUY buys its + legs and sells its − legs
    lots: int = Field(gt=0, le=1000)  # spread lots
    price_paise: int  # the spread price (may be negative)
    product: Product = Product.NRML


# --- the app -------------------------------------------------------------------------------


def create_app(
    platform: Platform,
    users: UserStore,
    settings: ServerSettings,
    *,
    broker_login: BrokerLogin | None = None,
    broker_status: Callable[[BrokerAccount], str | None] | None = None,
    alerts: Alerts | None = None,
    scheduler: Scheduler | None = None,
    reports: Reports | None = None,
    ticks: FileTickStore | None = None,
    replay_command: Callable[[date], str] | None = None,
    replay: ReplayFeed | None = None,
    backtests: Backtests | None = None,
    candles: CandleStore | None = None,
    # each firm's own imported candles (#396); without it, `candles` is every firm's
    candles_for: Callable[[str], CandleStore] | None = None,
    charts: Charts | None = None,
    # a server with a price source per subscription (#395): each source's own charts and
    # recorded days (#396)
    charts_for: Callable[[MarketData], Charts | None] | None = None,
    ticks_for: Callable[[MarketData], FileTickStore | None] | None = None,
    market_alerts: MarketAlerts | None = None,
    starter: AccountStarter | None = None,
    protection: ProtectionGuard | None = None,
    spreads: SpreadBook | None = None,  # each user's spread definitions (#414)
    monotonic: Callable[[], float] = time.monotonic,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="MockTrade", docs_url=None, redoc_url=None, openapi_url=None)
    # what the screens read from prices, one per subscription (#395): never another's
    markets = Markets(
        platform.instruments,
        charts_for=charts_for or (lambda md: charts if md is platform.market_data else None),
        ticks_for=ticks_for or (lambda md: ticks if md is platform.market_data else None),
    )
    no_prices = MarketData()  # someone with no subscription of their own: nothing ticks
    for source in platform.prices.all():  # from the start: no tick is missed before a screen asks
        markets.of(source)
    # spreads (#414): priced on every price source, from that source's own leg prices
    book = spreads if spreads is not None else SpreadBook(
        None, platform.instruments.get, platform.expiries.expired)  # fmt: skip
    app.state.spreads = book
    app.state.pricers = [SpreadPricer(md, book, platform.instruments.get)
                         for md in [*platform.prices.all(), no_prices]]  # fmt: skip

    def market(user: User) -> Market:
        found = platform.prices.for_user(user, users.accounts())
        return markets.of(found if found is not None else no_prices)

    hub = StreamHub(
        platform,
        users,
        origin=settings.origin,
        cookie_name=settings.cookie_name,
        alerts=alerts,
        market=market,
        market_alerts=market_alerts,
        spread_visible=lambda user, i: book.visible(user, i) is not None,
    )
    app.state.hub = hub
    METRICS.gauge("stream_connections", lambda: hub.connections)
    sources = platform.prices.all() or [platform.market_data]  # every subscription's (#396)
    METRICS.gauge("ticks_received", lambda: sum(md.ticks_received for md in sources))
    METRICS.gauge("instruments_watched", lambda: sum(len(md.watched()) for md in sources))
    process_gauges(METRICS)
    app.add_api_websocket_route("/ws", hub.serve)
    login_attempts: dict[str, deque[float]] = {}
    login_states: dict[str, tuple[str, str, float]] = {}  # state -> (user, account, expiry)

    # --- every request ------------------------------------------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        started = time.perf_counter_ns()
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get("origin") != settings.origin:
                return _headers(JSONResponse({"detail": "request from another site"}, 403))
            if not request.headers.get("content-type", "").startswith("application/json"):
                if request.method != "DELETE":
                    return _headers(JSONResponse({"detail": "send JSON"}, 415))
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            route = request.scope.get("route")
            name = getattr(route, "path", "other")  # the template: no ids in metric names
            METRICS.observe(f"http_us{{route={request.method} {name}}}", since_us(started))
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            # built files carry a content hash in their name: safe to cache for a year
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return _headers(response)

    def current_user(request: Request) -> User:
        user = users.session_user(request.cookies.get(settings.cookie_name))
        if user is None:
            raise HTTPException(401, "please log in")
        if user.must_change_password and request.url.path not in PASSWORD_FIRST:
            raise HTTPException(403, "set a new password first (an admin reset it)")
        return user

    Me = Annotated[User, Depends(current_user)]

    # --- workspaces (#368, docs/design/tenancy.md) ---------------------------------------------

    def in_workspace(user: User, account_id: str) -> bool:
        """The account exists and is in the user's own workspace."""
        account = users.get_account(account_id)
        return account is not None and account.workspace == user.workspace

    def admin_of(user: User, owner: str) -> bool:
        """An admin sees the backtests of their own workspace's users (#270, #368)."""
        if not user.has_role(Role.ADMIN):
            return False
        found = users.get_user(owner) if owner else None
        return found is not None and found.workspace == user.workspace

    def platform_wide(user: User, what: str) -> None:
        """Changes that touch every firm on the server (resetting metrics, a replay's clock,
        the shared recordings and candles) are an admin's only while the server holds one
        firm. With several, only the operator makes them, from the server's terminal."""
        if len(users.workspaces()) > 1:
            raise HTTPException(403, f"with several firms on this server, only its operator "
                                f"can {what}, from the server's terminal")  # fmt: skip

    def runtime(account_id: str, user: User, *, trade: bool = False) -> AccountRuntime:
        account = users.get_account(account_id)
        running = platform.account(account_id)
        if account is None or running is None or not can_view(user, account):
            raise HTTPException(404, "no such account")  # don't reveal other people's accounts
        # a turned-off account still lets its traders cancel and close: the risk checks pass
        # only closing trades on it (AccountRuntime.set_active)
        if trade and not can_manage(user, account):
            raise HTTPException(403, "you can view this account but not trade it")
        return running

    def audit(kind: str, detail: str, extra: dict[str, Any], account_id: str = "-") -> None:
        if platform.event_log is not None:
            platform.event_log.record_note(kind, account_id, detail, extra)

    # --- login ---------------------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION, "build": BUILD}  # the screens show it

    @app.get("/api/ready", response_model=None)
    async def ready() -> Any:
        """Readiness, apart from liveness (/api/health, #273): the critical loops run, the
        supervisor's watchdog ticks, and every account's state is rebuilt. Says only yes or
        no (it needs no login); the reasons are ops alerts on the screens."""
        ok, problems = platform.supervisor.ready()
        recovering = [a for a, r in platform.accounts.items()
                      if r.recovery is not None and r.recovery.state != "ok"]  # fmt: skip
        if ok and not recovering:
            return {"ready": True}
        return JSONResponse({"ready": False, "problems": len(problems) + len(recovering)},
                            status_code=503)  # fmt: skip

    @app.get("/api/metrics", response_model=None)
    async def metrics(user: Me, format: str = "json") -> dict[str, Any] | Response:
        """How fast each step is (metrics.py). Admin and risk only."""
        if not _risk_or_admin(user):
            raise HTTPException(403, "metrics are for admin and risk users")
        if format == "prometheus":
            return Response(METRICS.prometheus(), media_type="text/plain; version=0.0.4")
        return METRICS.snapshot()

    # --- options -------------------------------------------------------------------------------

    @app.get("/api/options")
    async def option_underlyings(user: Me) -> list[str]:
        return market(user).chains.underlyings()

    @app.get("/api/options/{underlying}/expiries")
    async def option_expiries(underlying: str, user: Me) -> list[str]:
        return [e.isoformat() for e in market(user).chains.expiries(underlying)]

    @app.get("/api/options/{underlying}/{expiry}/chain")
    async def option_chain(
        underlying: str, expiry: str, user: Me, strikes: int = 15
    ) -> dict[str, Any]:
        try:
            day = date.fromisoformat(expiry)
        except ValueError:
            raise HTTPException(422, "expiry must be a date like 2026-10-27") from None
        chains = market(user).chains
        if day not in chains.expiries(underlying):
            raise HTTPException(404, f"no options on {underlying} expiring {expiry}")
        return chains.snapshot(underlying, day, max(1, min(strikes, 40)))

    @app.get("/api/options/strategies")
    async def strategy_templates(user: Me) -> list[dict[str, Any]]:
        return strategy_templates_list()

    @app.post("/api/options/strategy/build")
    async def strategy_build(body: StrategyBuildIn, user: Me) -> dict[str, Any]:
        strategies = Strategies(market(user).chains)
        try:
            legs = strategies.build(body.code, body.underlying, body.expiry, body.width, body.lots)
            return strategies.analyse(legs)
        except StrategyError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/options/strategy/analyse")
    async def strategy_analyse(body: StrategyIn, user: Me) -> dict[str, Any]:
        legs = [{**leg.model_dump(exclude_none=True), "side": leg.side.value} for leg in body.legs]
        try:
            return Strategies(market(user).chains).analyse(legs)
        except StrategyError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- baskets -------------------------------------------------------------------------------

    def basket_legs(body: BasketIn) -> list[LegIn]:
        return [LegIn(**leg.model_dump()) for leg in body.legs]

    @app.post("/api/accounts/{account_id}/baskets/check")
    async def basket_check(account_id: str, body: BasketIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            return check_to_dict(await running.baskets.check(basket_legs(body), user.id))
        except BasketError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/accounts/{account_id}/baskets/send")
    async def basket_send(account_id: str, body: BasketIn, user: Me) -> JSONResponse:
        running = runtime(account_id, user, trade=True)
        try:
            result = await running.baskets.send(basket_legs(body), user.id)
        except BasketError as exc:
            raise HTTPException(422, str(exc)) from None
        if result["basket_id"] is None:  # a leg failed the check: nothing was sent
            return JSONResponse({"detail": "nothing sent", **check_to_dict(result["check"])}, 422)
        return JSONResponse(
            {
                "basket_id": result["basket_id"],
                "sent": [view_to_dict(v) for v in result["sent"]],
                "not_sent": result["not_sent"],
                "stopped": result["stopped"],
            },
            201,
        )

    # --- charges: the day's, an order's estimate, the published rates -------------------------

    def charges_service(running: AccountRuntime):
        if running.charges is None:
            raise HTTPException(404, "no charges model for this account")
        return running.charges

    @app.get("/api/charges/schedule")
    async def charge_schedule(user: Me) -> dict[str, Any]:
        return schedule_to_dict(platform.charges_model.schedule)

    @app.get("/api/accounts/{account_id}/charges")
    async def charges_today(account_id: str, user: Me) -> dict[str, Any]:
        service = charges_service(runtime(account_id, user))
        return {"account_id": account_id,
                **bill_to_dict(service.bill(), service.model.schedule)}  # fmt: skip

    @app.post("/api/accounts/{account_id}/charges/estimate")
    async def charges_estimate(account_id: str, body: ChargesIn, user: Me) -> dict[str, Any]:
        service = charges_service(runtime(account_id, user))
        try:
            bill = service.estimate(body.instrument_id, body.side, body.qty,
                                    body.price_paise, body.product)  # fmt: skip
        except ChargesError as exc:
            raise HTTPException(422, str(exc)) from None
        return bill_to_dict(bill, service.model.schedule)

    # --- research: recorded ticks (K11) ---------------------------------------------------------

    def recordings(user: User) -> FileTickStore:
        """The recorded days of the asker's own subscription (#396); on a simulator or a
        replay, MockTrade's own, everyone's."""
        found = market(user).ticks
        if found is None:
            raise HTTPException(404, "ticks aren't being recorded on this server")
        return found

    @app.get("/api/research/recordings")
    async def recorded_days(user: Me) -> dict[str, Any]:
        store = recordings(user)
        admin = user.has_role(Role.ADMIN)
        days = []
        for day in reversed(store.days()):
            s = await asyncio.to_thread(store.summary, day)
            row = {k: s[k] for k in ("day", "ticks", "bytes", "segments")}
            row["instruments"] = len(s["instruments"])
            row["deletable"] = admin and day != store.today()  # today is still being written
            if replay_command is not None:  # only where a replay of this server's days works
                row["replay"] = replay_command(day)
            days.append(row)
        return {"keep_days": store.keep_days, "days": days}

    @app.get("/api/research/recordings/{day}")
    async def recorded_day(day: str, user: Me) -> dict[str, Any]:
        store = recordings(user)
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, f"not a date: {day!r} (YYYY-MM-DD)") from None
        if when not in store.days():
            raise HTTPException(404, f"nothing recorded on {day}")
        return await asyncio.to_thread(store.summary, when)

    @app.delete("/api/research/recordings/{day}")
    async def recorded_day_delete(day: str, user: Me) -> dict[str, Any]:
        store = recordings(user)
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can delete recordings")
        if not platform.prices.per_account:  # MockTrade's own recordings: every firm's
            platform_wide(user, "delete recordings")
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, f"not a date: {day!r} (YYYY-MM-DD)") from None
        try:
            size = (await asyncio.to_thread(store.summary, when))["bytes"]
            await asyncio.to_thread(store.delete, when)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        audit("RECORDING_DELETED", f"{user.id} deleted the ticks of {day} ({size} bytes)", {})
        return {"day": day, "deleted": True}

    # --- backtests (K11.3) ---------------------------------------------------------------------

    def backtest_runs():
        if backtests is None:
            raise HTTPException(404, "backtests aren't set up on this server")
        return backtests

    @app.post("/api/research/backtests", status_code=201)
    async def backtest_start(body: BacktestIn, user: Me) -> dict[str, Any]:
        runs = backtest_runs()
        spec = BacktestSpec(tuple(body.days), body.algo, body.instrument_id, body.side.value,
                            body.qty, body.params, body.product.value, body.start,
                            body.capital_paise, body.name)  # fmt: skip
        try:
            run = runs.start(spec, candles=body.candles, owner=user.id)
        except BacktestBusy as exc:  # a bounded queue (#274): later, not never
            raise HTTPException(429, str(exc)) from None
        except BacktestError as exc:
            raise HTTPException(422, str(exc)) from None
        audit("BACKTEST", f"{user.id} started {run.run_id}: {body.algo} {body.side.value} "
              f"{body.qty} {body.instrument_id} over {len(body.days)} days", {})  # fmt: skip
        return run.to_dict(full=False)

    def own_run(run_id: str, user: User):
        """A backtest the user may see (#270): their own, or any for an admin. Someone else's
        is 'no backtest', exactly as one that doesn't exist: nothing leaks."""
        run = backtest_runs().get(run_id)
        if run is None or not run.may_see(user.id, admin_of(user, run.owner)):
            raise HTTPException(404, f"no backtest {run_id}")
        return run

    @app.get("/api/research/backtests")
    async def backtest_list(user: Me) -> list[dict[str, Any]]:
        admin = user.has_role(Role.ADMIN)
        return [r.to_dict(full=False) for r in backtest_runs().runs()
                if r.may_see(user.id, admin and admin_of(user, r.owner))]  # fmt: skip

    @app.get("/api/research/backtests/form")
    async def backtest_form_spec(user: Me, instrument_id: str = "") -> dict[str, Any]:
        backtest_runs()
        store = market(user).ticks  # their own subscription's days (#396)
        days = await asyncio.to_thread(store.days) if store is not None else []
        own = candle_store(user)
        out = form_spec(days, candles=own is not None)
        if own is not None and instrument_id:
            out["candle_days"] = await asyncio.to_thread(own.days, instrument_id)
        return out

    @app.get("/api/research/backtests/{run_id}/days/{day}", response_model=None)
    async def backtest_day(run_id: str, day: str, user: Me, format: str = "json") -> Any:
        if format not in ("json", "csv", "pdf"):
            raise HTTPException(422, "format must be json, csv or pdf")
        own_run(run_id, user)
        try:
            data = backtest_runs().day(run_id, day)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        if format == "json":
            return data
        name = f"mocktrade-backtest-{run_id}-{data['day']}"
        disposition = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
        if format == "csv":
            return Response(await asyncio.to_thread(report_csv, data),
                            media_type="text/csv; charset=utf-8", headers=disposition)  # fmt: skip
        return Response(await asyncio.to_thread(report_pdf, data), media_type="application/pdf",
                        headers=disposition)  # fmt: skip

    @app.get("/api/research/backtests/{run_id}")
    async def backtest_get(run_id: str, user: Me) -> dict[str, Any]:
        return own_run(run_id, user).to_dict()

    @app.post("/api/research/backtests/{run_id}/cancel")
    async def backtest_cancel(run_id: str, user: Me) -> dict[str, Any]:
        """Stop a queued or running backtest (#274): its owner or their firm's admin."""
        run = own_run(run_id, user)
        if not await backtest_runs().cancel(run_id):
            raise HTTPException(409, f"backtest {run_id} has already ended ({run.state})")
        audit("BACKTEST", f"{user.id} cancelled {run_id}", {})
        return run.to_dict(full=False)

    def candle_store(user: User) -> CandleStore | None:
        """The candles the asker's backtests read: their own firm's (#396)."""
        return candles_for(user.workspace) if candles_for is not None else candles

    @app.post("/api/research/candles", status_code=201)
    async def candles_import(body: CandlesIn, user: Me) -> dict[str, Any]:
        store = candle_store(user)
        if store is None:
            raise HTTPException(404, "candles can't be imported on this server")
        if not user.has_role(Role.ADMIN):  # the firm's dataset: all its backtests read it (#270)
            raise HTTPException(403, "only an admin can import candles (everyone's backtests "
                                "use them)")  # fmt: skip
        if candles_for is None:  # one dataset for every firm on the server
            platform_wide(user, "import candles")
        if platform.instruments.get(body.instrument_id) is None:
            raise HTTPException(422, f"unknown instrument {body.instrument_id}")
        try:
            n = await asyncio.to_thread(store.put, body.instrument_id, body.day, body.rows)
        except BacktestError as exc:
            raise HTTPException(422, str(exc)) from None
        audit("CANDLES_IMPORTED", f"{user.id} imported {n} candles for {body.instrument_id} on "
              f"{body.day.isoformat()}", {"by": user.id, "instrument_id": body.instrument_id,
                                          "day": body.day.isoformat(), "rows": n})  # fmt: skip
        return {"instrument_id": body.instrument_id, "day": body.day.isoformat(), "rows": n}

    # --- charts (K12.1) ---------------------------------------------------------------------

    def chart_service(user: User) -> Charts:
        found = market(user).charts  # the asker's own subscription's candles (#395)
        if found is None:
            raise HTTPException(404, "charts aren't set up on this server")
        return found

    def chart_time(text: str, name: str) -> datetime | None:
        if not text:
            return None
        try:
            at = datetime.fromisoformat(text)
        except ValueError:
            raise HTTPException(422, f"{name}: not a time: {text!r} (ISO 8601)") from None
        return at if at.tzinfo else at.replace(tzinfo=IST)  # a time without a zone is India's

    def chart_instrument(instrument_id: str) -> str:
        if platform.instruments.get(instrument_id) is None:
            raise HTTPException(404, f"unknown instrument {instrument_id}")
        return instrument_id

    @app.get("/api/charts/spec")
    async def chart_spec(user: Me) -> dict[str, Any]:
        return chart_service(user).spec()

    @app.get("/api/charts/{instrument_id}/candles")
    async def chart_candles(instrument_id: str, user: Me, interval: str = "1m", start: str = "",
                            end: str = "") -> dict[str, Any]:  # fmt: skip
        service = chart_service(user)
        first, last = chart_time(start, "start"), chart_time(end, "end")
        try:
            return await service.candles(chart_instrument(instrument_id), interval, first, last)
        except ChartError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/accounts/{account_id}/charts/{instrument_id}/marks")
    async def chart_marks(account_id: str, instrument_id: str, user: Me, start: str = "",
                          end: str = "") -> dict[str, Any]:  # fmt: skip
        runtime(account_id, user)
        service = chart_service(user)
        last = chart_time(end, "end") or datetime.now(IST) + timedelta(minutes=1)
        first = chart_time(start, "start") or last - timedelta(days=1)
        marks = await asyncio.to_thread(service.marks, account_id, chart_instrument(instrument_id),
                                        first, last)  # fmt: skip
        return {"account_id": account_id, "instrument_id": instrument_id, "marks": marks}

    # --- market alerts (K12.3): each user's own ------------------------------------------------

    def alert_service() -> MarketAlerts:
        if market_alerts is None:
            raise HTTPException(404, "alerts aren't set up on this server")
        return market_alerts

    @app.get("/api/alerts/spec")
    async def alerts_spec(user: Me) -> dict[str, Any]:
        alert_service()
        return alert_spec()

    @app.get("/api/alerts")
    async def alerts_list(user: Me) -> list[dict[str, Any]]:
        return alert_service().list(user.id)

    @app.post("/api/alerts", status_code=201)
    async def alerts_create(body: AlertIn, user: Me) -> dict[str, Any]:
        try:
            return alert_service().create(user.id, body.model_dump())
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/alerts/webhook")
    async def alerts_webhook(user: Me) -> dict[str, Any]:
        return alert_service().webhook(user.id)

    @app.put("/api/alerts/webhook")
    async def alerts_webhook_set(body: WebhookIn, user: Me) -> dict[str, Any]:
        try:
            return alert_service().set_webhook(user.id, body.url)
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete("/api/alerts/webhook", status_code=204)
    async def alerts_webhook_clear(user: Me) -> Response:
        alert_service().clear_webhook(user.id)
        return Response(status_code=204)

    @app.post("/api/alerts/webhook/test")
    async def alerts_webhook_test(user: Me) -> dict[str, Any]:
        try:
            error = await alert_service().test_webhook(user.id)
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": not error, "error": error}

    # (after the webhook routes: /api/alerts/webhook is not an alert id)
    @app.patch("/api/alerts/{alert_id}")
    async def alerts_update(alert_id: str, body: AlertPatch, user: Me) -> dict[str, Any]:
        try:
            return alert_service().update(user.id, alert_id, body.model_dump(exclude_unset=True))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete("/api/alerts/{alert_id}", status_code=204)
    async def alerts_delete(alert_id: str, user: Me) -> Response:
        try:
            alert_service().delete(user.id, alert_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        return Response(status_code=204)

    # --- a replay server's controls (K11.2) ------------------------------------------------------

    @app.get("/api/replay")
    async def replay_status(user: Me) -> dict[str, Any]:
        if replay is None:
            raise HTTPException(404, "this server isn't replaying a recorded day")
        return replay.status()

    @app.post("/api/replay")
    async def replay_control(body: ReplayIn, user: Me) -> dict[str, Any]:
        if replay is None:
            raise HTTPException(404, "this server isn't replaying a recorded day")
        platform_wide(user, "control the replay")
        try:
            if body.action == "pause":
                replay.pause()
            elif body.action == "resume":
                replay.resume()
            elif body.action == "speed":
                replay.set_speed(body.speed if body.speed is not None else -1)
            else:
                if body.at is None:
                    raise ValueError("seek needs a time (at)")
                replay.seek(body.at)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        audit("REPLAY_CONTROL", f"{user.id}: {body.action}", body.model_dump(mode="json"))
        return replay.status()

    # --- MTM limits and the square-off assistant --------------------------------------------------

    def mtm_watch(running: AccountRuntime):
        if running.mtm is None:
            raise HTTPException(404, "no MTM watch for this account")
        return running.mtm

    @app.get("/api/accounts/{account_id}/mtm")
    async def mtm_rules(account_id: str, user: Me) -> dict[str, Any]:
        return mtm_watch(runtime(account_id, user)).to_dict()

    @app.put("/api/accounts/{account_id}/mtm/positions")
    async def mtm_position(account_id: str, body: PositionRuleIn, user: Me) -> dict[str, Any]:
        watch = mtm_watch(runtime(account_id, user, trade=True))
        rule = PositionRule(body.instrument_id, body.product.value, body.max_loss_paise,
                            body.max_loss_pct, body.auto_close)  # fmt: skip
        try:
            watch.set_position_rule(rule, user.id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        watch.check()
        return watch.to_dict()

    @app.delete("/api/accounts/{account_id}/mtm/positions")
    async def mtm_position_off(account_id: str, instrument_id: str, product: Product,
                               user: Me) -> dict[str, Any]:  # fmt: skip
        watch = mtm_watch(runtime(account_id, user, trade=True))
        watch.remove_position_rule(f"{instrument_id}|{product.value}", user.id)
        return watch.to_dict()

    @app.post("/api/accounts/{account_id}/mtm/retry")
    async def mtm_retry(account_id: str, body: MtmRetryIn, user: Me) -> dict[str, Any]:
        """Close a blocked MTM close again (#263): only one that nothing is still working on."""
        watch = mtm_watch(runtime(account_id, user, trade=True))
        if not body.confirm:
            raise HTTPException(422, "a retry needs a confirmation")
        try:
            await watch.retry(body.key, user.id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return watch.to_dict()

    @app.put("/api/accounts/{account_id}/mtm/portfolio")
    async def mtm_portfolio(account_id: str, body: PortfolioRuleIn, user: Me) -> dict[str, Any]:
        watch = mtm_watch(runtime(account_id, user, trade=True))
        try:
            watch.set_portfolio_rule(
                PortfolioRule(body.max_loss_paise, body.target_paise, body.auto_close), user.id
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        watch.check()
        return watch.to_dict()

    @app.delete("/api/accounts/{account_id}/mtm/portfolio")
    async def mtm_portfolio_off(account_id: str, user: Me) -> dict[str, Any]:
        watch = mtm_watch(runtime(account_id, user, trade=True))
        watch.set_portfolio_rule(None, user.id)
        return watch.to_dict()

    @app.get("/api/accounts/{account_id}/square-off")
    async def square_off_status(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.squareoff is None:
            raise HTTPException(404, "no square-off assistant for this account")
        return running.squareoff.status()

    @app.post("/api/accounts/{account_id}/square-off")
    async def square_off(account_id: str, body: ConfirmIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        if running.squareoff is None:
            raise HTTPException(404, "no square-off assistant for this account")
        if not body.confirm:
            raise HTTPException(422, "square-off needs the trader's confirmation")
        return {"closed": await running.squareoff.close_all(user.id)}

    # --- lost stops: what is unprotected, and restoring a stop (#264) ---------------------------

    @app.get("/api/accounts/{account_id}/protection")
    async def protection_status(account_id: str, user: Me) -> dict[str, Any]:
        runtime(account_id, user)
        rows = protection.status(account_id) if protection is not None else []
        return {"unprotected": rows, "guarantee": list(GUARANTEE)}

    @app.post("/api/accounts/{account_id}/protection/restore")
    async def protection_restore(account_id: str, body: RestoreIn, user: Me) -> dict[str, Any]:
        runtime(account_id, user, trade=True)  # only someone who trades this account
        if protection is None:
            raise HTTPException(404, "no protection guard on this server")
        if not body.confirm:
            raise HTTPException(422, "restoring a stop needs a confirmation")
        try:
            view = protection.restore(account_id, body.instrument_id, user.id)
        except RestoreError as exc:
            raise HTTPException(409, str(exc)) from None
        except AlgoError as exc:
            raise HTTPException(422, exc.reason) from None
        return algo_to_dict(view)

    # --- flatten: closing positions on purpose, STOP or not (#262) ------------------------------

    def flattener(account_id: str, user: User):
        running = runtime(account_id, user)
        account = users.get_account(account_id)
        if not ((account and can_manage(user, account)) or _risk_or_admin(user)):
            raise HTTPException(403, "you can't close positions on this account")
        if running.flatten is None:
            raise HTTPException(404, "no flatten for this account")
        return running.flatten

    @app.get("/api/accounts/{account_id}/flatten")
    async def flatten_status(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.flatten is None:
            raise HTTPException(404, "no flatten for this account")
        return running.flatten.status()

    @app.post("/api/accounts/{account_id}/flatten")
    async def flatten(account_id: str, body: FlattenIn, user: Me) -> dict[str, Any]:
        service = flattener(account_id, user)
        if not body.confirm:
            raise HTTPException(422, "flatten needs a confirmation")
        return await service.run(user.id, instrument_id=body.instrument_id,
                                 product=body.product)  # fmt: skip

    # --- margin: what an order would block, against what's available -------------------------

    @app.post("/api/accounts/{account_id}/margin")
    async def order_margin(account_id: str, body: ChargesIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.margin is None:
            raise HTTPException(404, "no margin quote for this account")
        request = OrderRequest("MQ", account_id, user.id, body.instrument_id, body.side,
                               body.qty, OrderType.LIMIT, body.product,
                               price_paise=body.price_paise)  # fmt: skip
        result = await running.margin.check(request)
        return {
            "exit": result.exit,
            "required_paise": result.required_paise,
            "available_paise": result.available_paise,
            "enough": result.enough,
            "note": result.note,
            "checked_before_sending": running.margin_check,
        }

    # --- end-of-day reports and P&L history ---------------------------------------------------

    day_reports = reports or Reports(platform, None)

    def report_day(text: str) -> date:
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise HTTPException(422, f"not a date: {text!r} (YYYY-MM-DD)") from None

    @app.get("/api/accounts/{account_id}/reports")
    async def report_days(account_id: str, user: Me) -> dict[str, Any]:
        runtime(account_id, user)
        return {"today": day_reports.today().isoformat(),
                "kept": [d.isoformat() for d in day_reports.kept_days(account_id)]}  # fmt: skip

    @app.get("/api/accounts/{account_id}/reports/{day}", response_model=None)
    async def report(account_id: str, day: str, user: Me, format: str = "json") -> Any:
        runtime(account_id, user)
        if format not in ("json", "csv", "pdf"):
            raise HTTPException(422, "format must be json, csv or pdf")
        when = report_day(day)
        try:  # read and built off the trading loop (#274): a long day never stalls it
            data = await asyncio.to_thread(day_reports.day, account_id, when)
        except ReportError as exc:
            raise HTTPException(422, str(exc)) from None
        if format == "json":
            return data
        name = f"mocktrade-{account_id}-{data['day']}{'' if data['final'] else '-preview'}"
        disposition = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
        if format == "csv":
            return Response(await asyncio.to_thread(report_csv, data),
                            media_type="text/csv; charset=utf-8", headers=disposition)  # fmt: skip
        return Response(await asyncio.to_thread(report_pdf, data), media_type="application/pdf",
                        headers=disposition)  # fmt: skip

    @app.get("/api/accounts/{account_id}/pnl")
    async def pnl(account_id: str, user: Me, by: str = "day", start: str = "",
                  end: str = "") -> dict[str, Any]:  # fmt: skip
        runtime(account_id, user)
        last = report_day(end) if end else day_reports.today()
        first = report_day(start) if start else last - timedelta(days=30)
        try:
            found = await asyncio.to_thread(day_reports.history, account_id, by, first, last)
            return {"account_id": account_id, **found}
        except ReportError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- funds, holdings, tradebook (read-only, from the broker) ------------------------------

    async def portfolio(account_id: str, kind: str, fresh: bool, user: User) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.portfolio is None:
            raise HTTPException(404, f"{running.broker.name} doesn't report {kind}")
        try:
            snapshot = await running.portfolio.read(kind, fresh=fresh)
        except PortfolioError as exc:
            raise HTTPException(404, str(exc)) from None
        return snapshot_to_dict(account_id, snapshot)

    @app.get("/api/accounts/{account_id}/funds")
    async def funds(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "funds", fresh, user)

    @app.get("/api/accounts/{account_id}/holdings")
    async def holdings(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "holdings", fresh, user)

    @app.get("/api/accounts/{account_id}/trades")
    async def trades(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "trades", fresh, user)

    # --- GTT (held by the broker) ------------------------------------------------------------

    def gtt_service(running: AccountRuntime):
        if running.gtt is None:
            raise HTTPException(404, f"{running.broker.name} doesn't hold GTT orders")
        return running.gtt

    def gtt_request(body: GttIn, account_id: str, user: User):
        return gtt_request_from(body.model_dump(mode="json"), account_id, user.id)

    @app.get("/api/accounts/{account_id}/gtt")
    async def gtt_list(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [gtt_to_dict(v) for v in gtt_service(runtime(account_id, user)).list()]

    @app.get("/api/accounts/{account_id}/gtt-spec")
    async def gtt_spec(account_id: str, instrument_id: str, user: Me) -> dict[str, Any]:
        """What the broker allows for GTTs on this instrument: the screens draw from it."""
        try:
            return gtt_spec_to_dict(gtt_service(runtime(account_id, user)).spec(instrument_id))
        except GttError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/api/accounts/{account_id}/gtt", status_code=201)
    async def gtt_place(account_id: str, body: GttIn, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.place(gtt_request(body, account_id, user)))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.put("/api/accounts/{account_id}/gtt/{gtt_id}")
    async def gtt_modify(account_id: str, gtt_id: str, body: GttIn, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.modify(gtt_id, gtt_request(body, account_id, user)))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete("/api/accounts/{account_id}/gtt/{gtt_id}")
    async def gtt_delete(account_id: str, gtt_id: str, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.delete(gtt_id, user.id))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/ops")
    async def ops(user: Me) -> dict[str, Any]:
        """What operations look like now: active alerts and the scheduled jobs that ran."""
        if not _risk_or_admin(user):
            raise HTTPException(403, "operations are for admin and risk users")
        mine = {a.id for a in users.accounts() if a.workspace == user.workspace}
        return {
            "alerts": [
                alert_to_dict(a)
                for a in alerts.active()
                if a.account_id is None or a.account_id in mine
            ]
            if alerts
            else [],
            "jobs": [
                {"job": name, "at": at.isoformat()}
                for name, at in (scheduler.ran if scheduler else [])
            ][-50:],
        }

    @app.delete("/api/metrics")
    async def reset_metrics(user: Me) -> dict[str, str]:
        """Start measuring afresh (e.g. after a load test's warm-up). Admin only."""
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can reset metrics")
        platform_wide(user, "reset the metrics")
        METRICS.reset()
        audit("METRICS_RESET", user.id, {})
        return {"status": "reset"}

    @app.post("/api/session")
    async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        ip = request.client.host if request.client else "?"
        now = monotonic()
        attempts = login_attempts.setdefault(ip, deque())
        while attempts and attempts[0] <= now - LOGIN_WINDOW_S:
            attempts.popleft()
        if len(attempts) >= LOGIN_LIMIT_PER_IP:
            raise HTTPException(429, "too many login attempts from this address")
        attempts.append(now)
        try:
            user = await asyncio.to_thread(
                users.verify_login, body.username, body.password, body.code
            )
        except LoginFailed as exc:
            audit("LOGIN_FAILED", body.username, {"ip": ip})
            raise HTTPException(401, str(exc)) from None
        token = users.start_session(user, device=request.headers.get("user-agent", ""), ip=ip,
                                    stepped_up=True)  # it just checked the code  # fmt: skip
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=int(SESSION_MAX.total_seconds()),
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        audit("LOGIN_OK", user.id, {"ip": ip})
        return _me(user, users, platform, broker_status)

    @app.delete("/api/session", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        users.end_session(request.cookies.get(settings.cookie_name))
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.cookie_secure)

    @app.get("/api/me")
    async def me(user: Me) -> dict[str, Any]:
        return _me(user, users, platform, broker_status)

    @app.post("/api/me/password", status_code=204)
    async def change_password(body: PasswordIn, user: Me, request: Request) -> None:
        """The user's own change (also the first step after an admin's reset). Their other
        sessions end; this one stays."""
        try:
            await asyncio.to_thread(users.change_own_password, user.id, body.old, body.new,
                                    request.cookies.get(settings.cookie_name, ""))  # fmt: skip
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        audit("PASSWORD_CHANGED", f"{user.id} changed their password", {"user_id": user.id})

    # --- step-up and the user's own sessions (#363) ----------------------------------------------

    def require_step_up(request: Request, what: str) -> None:
        """Privileged changes need a fresh authenticator code, not just the session: a stolen
        session alone can't loosen limits, add traders, reset others' logins or resume the
        firm. Never asked for STOP, Flatten or cancels (reducing risk is never blocked)."""
        if not users.stepped_up(request.cookies.get(settings.cookie_name)):
            raise HTTPException(428, f"step-up: enter a fresh authenticator code to {what}")

    @app.post("/api/me/step-up", status_code=204)
    async def step_up(body: CodeIn, user: Me, request: Request) -> None:
        ip = request.client.host if request.client else "?"
        try:
            await asyncio.to_thread(users.step_up, request.cookies.get(settings.cookie_name, ""),
                                    body.code)  # fmt: skip
        except LoginFailed as exc:
            audit("STEP_UP_FAILED", f"{user.id}: wrong authenticator code", {"user_id": user.id,
                                                                             "ip": ip})  # fmt: skip
            raise HTTPException(401 if str(exc) == "please log in" else 403, str(exc)) from None
        audit("STEP_UP_OK", f"{user.id} confirmed with a fresh code", {"user_id": user.id,
                                                                       "ip": ip})  # fmt: skip

    @app.get("/api/me/sessions")
    async def my_sessions(user: Me, request: Request) -> list[dict[str, Any]]:
        return users.own_sessions(user.id, request.cookies.get(settings.cookie_name))

    @app.delete("/api/me/sessions/{session_id}", status_code=204)
    async def end_my_session(session_id: str, user: Me) -> None:
        if not users.end_own_session(user.id, session_id):
            raise HTTPException(404, "no such session")  # someone else's is the same answer
        audit("SESSION_ENDED", f"{user.id} ended one of their sessions",
              {"user_id": user.id, "session": session_id})  # fmt: skip

    app.include_router(admin_router(platform, users, current_user, audit, starter,
                                    require_step_up))  # fmt: skip

    # --- spreads (#414): each user's own multi-leg definitions --------------------------------

    def _legs(body: SpreadIn) -> list[SpreadLeg]:
        return [SpreadLeg(x.instrument_id, x.ratio, x.multiplier, x.quote, x.payup_ticks)
                for x in body.legs]  # fmt: skip

    def _spread_out(s: SpreadDef) -> dict[str, Any]:
        return {**spread_to_dict(s), "tick_paise": spread_tick(s.legs, platform.instruments.get)}

    @app.get("/api/spreads")
    async def list_spreads(user: Me) -> list[dict[str, Any]]:
        return [_spread_out(s) for s in book.mine(user)]

    @app.post("/api/spreads", status_code=201)
    async def create_spread(body: SpreadIn, user: Me) -> dict[str, Any]:
        try:
            made = book.create(user, body.name, _legs(body))
        except SpreadError as exc:
            raise HTTPException(422, str(exc)) from None
        await book.save(user.id)
        return _spread_out(made)

    @app.put("/api/spreads/{spread_id}")
    async def update_spread(spread_id: str, body: SpreadIn, user: Me) -> dict[str, Any]:
        try:
            changed = book.update(user, spread_id, body.name, _legs(body))
        except SpreadError as exc:
            raise HTTPException(404 if exc.missing else 422, str(exc)) from None
        await book.save(user.id)
        return _spread_out(changed)

    @app.post("/api/accounts/{account_id}/spreads/{spread_id}/orders", status_code=201)
    async def trade_spread(
        account_id: str, spread_id: str, body: SpreadOrderIn, user: Me
    ) -> dict[str, Any]:
        """Buy or sell spread lots at a spread price: the ASE algo (#415) quotes the quoting
        leg and hedges the others. The server works out the legs; the screens never do."""
        running = runtime(account_id, user, trade=True)
        spread = book.visible(user, PREFIX + spread_id)
        if spread is None:
            raise HTTPException(404, f"no spread {spread_id}")
        q = spread.quote_leg
        instrument = platform.instruments.get(q.instrument_id)
        if instrument is None:
            raise HTTPException(422, f"{q.instrument_id} is not a known contract")
        buys_q = (q.ratio > 0) == (body.side is Side.BUY)
        try:
            view = running.algos.start(
                ASE.code,
                user_id=user.id,
                instrument_id=q.instrument_id,
                side=Side.BUY if buys_q else Side.SELL,
                qty=body.lots * abs(q.ratio) * instrument.lot_size,
                params={"spread": spread.spread_id, "level_paise": body.price_paise},
                product=body.product,
            )
        except AlgoError as exc:
            raise HTTPException(422, exc.reason) from None
        return algo_to_dict(view)

    @app.delete("/api/spreads/{spread_id}", status_code=204)
    async def delete_spread(spread_id: str, user: Me) -> None:
        try:
            book.delete(user, spread_id)
        except SpreadError as exc:
            raise HTTPException(404, str(exc)) from None
        await book.save(user.id)

    # --- instruments and layouts -------------------------------------------------------------

    @app.get("/api/instruments")
    async def search_instruments(user: Me, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        live = platform.expiries
        found = platform.instruments.search(q, limit, keep=lambda i: not live.expired(i))
        return [_instrument(i, live) for i in found]  # expired contracts don't come up (#290)

    @app.get("/api/instruments/{instrument_id}")
    async def get_instrument(instrument_id: str, user: Me) -> dict[str, Any]:
        instrument = platform.instruments.get(instrument_id)
        if instrument is None and (spread := book.visible(user, instrument_id)) is not None:
            tick = market(user).prices.latest(instrument_id)  # its implied price, if watched
            found = _spread_instrument(spread, platform.instruments.get)
            return {**found, "ltp_paise": tick.ltp_paise if tick else None}
        if instrument is None:
            raise HTTPException(404, "no such instrument")
        tick = market(user).prices.latest(instrument_id)  # the asker's own price (#395)
        found = _instrument(instrument, platform.expiries)
        return {**found, "ltp_paise": tick.ltp_paise if tick else None}

    @app.get("/api/instruments/{instrument_id}/vap")
    async def get_vap(instrument_id: str, user: Me) -> dict[str, Any]:
        """Today's volume at each price (an estimate from snapshots, see marketdata/vap.py)."""
        if platform.instruments.get(instrument_id) is None:
            raise HTTPException(404, "no such instrument")
        volume, levels = market(user).prices.vap.snapshot(instrument_id)
        return {"volume": volume, "levels": [[p, q] for p, q in sorted(levels.items())]}

    @app.get("/api/instruments/{instrument_id}/prints")
    async def instrument_prints(instrument_id: str, user: Me, after: int = 0,
                                limit: int = 100) -> dict[str, Any]:  # fmt: skip
        if platform.instruments.get(instrument_id) is None:
            raise HTTPException(404, f"unknown instrument {instrument_id}")
        mine = market(user)
        rows = mine.prints.recent(instrument_id, after=max(0, after),
                                  limit=max(1, min(limit, 300)))  # fmt: skip
        return {"instrument_id": instrument_id, "prints": [print_to_dict(p) for p in rows],
                "kept": mine.prints.keep, "watched": mine.prices.is_watched(instrument_id),
                "epoch": mine.prints.epoch,  # a new one: the numbering started again (#277)
                "estimate": "estimated from price snapshots: the quantity is the volume traded "
                            "between two snapshots (two trades in one show as one), the time is "
                            "the snapshot's last trade, and the side is inferred from the touch "
                            "before the trade"}  # fmt: skip

    @app.get("/api/market/movers")
    async def market_movers(user: Me, by: str = "gainers", limit: int = 10, exchange: str = "",
                            kind: str = "") -> dict[str, Any]:  # fmt: skip
        try:
            return market(user).movers.top(by, limit=limit, exchange=exchange, kind=kind)
        except MoversError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/accounts/{account_id}/instruments/{instrument_id}/order-spec")
    async def get_order_spec(account_id: str, instrument_id: str, user: Me) -> dict[str, Any]:
        """What can be ordered on this instrument through this account's broker: the order
        types, what each needs, validities, products, tick and lot (domain/order_spec.py).
        The screens draw their tickets from this; nothing broker-specific lives in the UI."""
        running = runtime(account_id, user)
        instrument = platform.instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "no such instrument")
        expiry = expiry_to_dict(platform.expiries.info(instrument))  # warnings, cut-off (#290)
        return {**spec_to_dict(spec_of(running.broker)(instrument)), "expiry": expiry}

    @app.get("/api/layouts")
    async def list_layouts(user: Me) -> list[dict[str, str]]:
        return [{"name": n, "updated_at": t} for n, t in users.layouts(user.id)]

    @app.get("/api/layouts/{name}")
    async def get_layout(name: str, user: Me) -> dict[str, Any]:
        layout = users.get_layout(user.id, name)
        if layout is None:
            raise HTTPException(404, "no such layout")
        return {"name": name, "layout": json.loads(layout)}

    @app.put("/api/layouts/{name}")
    async def save_layout(name: str, body: LayoutIn, user: Me) -> dict[str, str]:
        try:
            users.save_layout(user.id, name, json.dumps(body.layout, separators=(",", ":")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"name": name}

    @app.delete("/api/layouts/{name}", status_code=204)
    async def delete_layout(name: str, user: Me) -> None:
        if not users.delete_layout(user.id, name):
            raise HTTPException(404, "no such layout")

    # --- the user's own screen settings: shortcuts (K13.4), theme (K13.5) -----------------------

    @app.get("/api/me/settings/{name}")
    async def get_setting(name: str, user: Me) -> dict[str, Any]:
        """The value as the screens saved it, or null: nothing saved (the defaults)."""
        try:
            value = users.get_setting(user.id, name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"name": name, "value": json.loads(value) if value is not None else None}

    @app.put("/api/me/settings/{name}")
    async def put_setting(name: str, body: SettingIn, user: Me) -> dict[str, Any]:
        """Kept as given (the screens own its shape); null forgets it: back to the defaults."""
        value = None if body.value is None else json.dumps(body.value, separators=(",", ":"))
        try:
            users.set_setting(user.id, name, value)
        except ValueError as exc:
            code = 404 if "no setting" in str(exc) else 422
            raise HTTPException(code, str(exc)) from None
        return {"name": name, "value": body.value}

    # --- orders ------------------------------------------------------------------------------

    @app.get("/api/accounts/{account_id}/orders")
    async def list_orders(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [view_to_dict(v) for v in runtime(account_id, user).orders.orders()]

    @app.post("/api/accounts/{account_id}/orders", status_code=201)
    async def place(account_id: str, body: OrderIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        request = OrderRequest(
            client_order_id=body.client_order_id,
            account_id=account_id,
            user_id=user.id,  # from the session, never from the body
            instrument_id=body.instrument_id,
            side=body.side,
            qty=body.qty,
            order_type=body.order_type,
            product=body.product,
            validity=body.validity,
            price_paise=body.price_paise,
            trigger_paise=body.trigger_paise,
            market_protection=body.market_protection,
        )
        try:
            return view_to_dict(await running.orders.submit(request))
        except OrderError as exc:  # e.g. an order id reused for a different order (#279)
            raise HTTPException(409, exc.reason) from None

    @app.patch("/api/accounts/{account_id}/orders/{client_order_id}")
    async def modify(
        account_id: str, client_order_id: str, body: ModifyIn, user: Me
    ) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            view = await running.orders.modify(
                client_order_id,
                qty=body.qty,
                price_paise=body.price_paise,
                trigger_paise=body.trigger_paise,
            )
        except OrderError as exc:
            raise HTTPException(409, exc.reason) from None
        return view_to_dict(view)

    @app.delete("/api/accounts/{account_id}/orders/{client_order_id}")
    async def cancel(account_id: str, client_order_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            return view_to_dict(await running.orders.cancel(client_order_id))
        except OrderError as exc:
            raise HTTPException(409, exc.reason) from None

    @app.post("/api/accounts/{account_id}/orders/{client_order_id}/settle")
    async def settle(account_id: str, client_order_id: str, body: ConfirmIn,
                     user: Me) -> dict[str, Any]:  # fmt: skip
        """An unanswered order a person checked at the broker: not there, so never placed
        (#259). Only after confirming; recorded with who did it."""
        running = runtime(account_id, user, trade=True)
        if not body.confirm:
            raise HTTPException(422, "check the broker's own order book first, then confirm")
        try:
            return view_to_dict(running.orders.settle_unconfirmed(client_order_id, by=user.id))
        except OrderError as exc:
            raise HTTPException(409, exc.reason) from None

    @app.get("/api/accounts/{account_id}/orders/{client_order_id}/history")
    async def history(account_id: str, client_order_id: str, user: Me) -> list[dict[str, Any]]:
        running = runtime(account_id, user)
        if platform.event_log is None or running.orders.get(client_order_id) is None:
            raise HTTPException(404, "no such order")
        events = await asyncio.to_thread(platform.event_log.history, client_order_id)
        return [_event(e) for e in events if e.account_id == account_id]

    @app.get("/api/accounts/{account_id}/events")
    async def account_events(
        account_id: str,
        user: Me,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The account's audit trail, newest first: its orders, its kill switch and the
        firm's (see EventLog.page for the paging)."""
        runtime(account_id, user)
        if platform.event_log is None:
            return []
        limit = max(1, min(limit, 1000))
        names = [account_id, f"account {account_id}", platform.firm_switch_of(user.workspace).name]
        events = await asyncio.to_thread(
            platform.event_log.page,
            names,
            before_seq=before_seq,
            after_seq=after_seq,
            limit=limit,
        )
        return [_event(e) for e in events]

    # --- algos -----------------------------------------------------------------------------
    # Specs describe each algo's settings; the screen draws its form from them (algos/spec.py).

    @app.get("/api/accounts/{account_id}/algo-specs")
    async def algo_specs(account_id: str, user: Me) -> list[dict[str, Any]]:
        engine = runtime(account_id, user).algos
        return [
            {**algo_spec_to_dict(t.spec), "exchange_algo_id": t.exchange_algo_id,
             "exchange_algo_note": algo_id_note(t)}  # said, not hidden (#291)
            for t in engine.types.values()
        ]  # fmt: skip

    @app.get("/api/accounts/{account_id}/algos")
    async def list_algos(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [algo_to_dict(a) for a in runtime(account_id, user).algos.algos()]

    @app.post("/api/accounts/{account_id}/algos", status_code=201)
    async def start_algo(account_id: str, body: AlgoIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            view = running.algos.start(
                body.code,
                user_id=user.id,  # from the session, never from the body
                instrument_id=body.instrument_id,
                side=body.side,
                qty=body.qty,
                params=body.params,
                product=body.product,
            )
        except AlgoError as exc:
            raise HTTPException(422, exc.reason) from None
        return algo_to_dict(view)

    @app.post("/api/accounts/{account_id}/algos/{algo_id}/{action}")
    async def control_algo(account_id: str, algo_id: str, action: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        engine = running.algos
        controls = {"pause": engine.pause, "resume": engine.resume, "stop": engine.stop}
        if action not in controls:
            raise HTTPException(404, "unknown action: use pause, resume or stop")
        try:
            view = controls[action](algo_id)
        except AlgoError as exc:
            raise HTTPException(409, exc.reason) from None
        return algo_to_dict(view)

    # --- positions -----------------------------------------------------------------------------

    @app.get("/api/accounts/{account_id}/positions")
    async def positions(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        book = running.positions
        last = running.reconciler.last
        return {
            "positions": [position_to_dict(p) for p in book.positions()],
            "realised_paise": book.realised_paise(),
            "unrealised_paise": book.unrealised_paise(),
            "day_pnl_paise": book.day_pnl_paise(),
            "recovery": (
                {"state": running.recovery.state, "reason": running.recovery.reason}
                if running.recovery is not None
                else None
            ),
            "reconcile": {
                "checked_at": last.checked_at.isoformat() if last else None,
                "ok": last.ok if last else None,
                "error": last.error if last else "",
                "alerts": [
                    {
                        "instrument_id": m.instrument_id,
                        "product": m.product.value if m.product else None,  # None: the net
                        "ours": m.ours,
                        "broker": m.broker,
                        "since": m.since.isoformat(),
                        "message": m.message,
                    }
                    for m in running.reconciler.alerts()
                ],
            },
        }

    # --- surveillance and the auditor's export (#291) ---------------------------------------------

    def _risk_only(user: User) -> None:
        if not _risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can see surveillance alerts")

    @app.get("/api/surveillance")
    async def surveillance_alerts(user: Me, open_only: bool = False) -> list[dict[str, Any]]:
        """Patterns worth a look (risk/surveillance.py): self-trades, large orders, cancel
        ratios, bursts. Kept in the audit trail; acknowledged with a note."""
        _risk_only(user)
        return [surveillance_to_dict(a) for a in platform.surveillance.alerts(open_only=open_only)
                if in_workspace(user, a.account_id)]  # fmt: skip

    @app.post("/api/surveillance/{alert_id}/ack")
    async def acknowledge_alert(alert_id: str, body: AckIn, user: Me) -> dict[str, Any]:
        _risk_only(user)
        found = next((a for a in platform.surveillance.alerts() if a.alert_id == alert_id), None)
        if found is None or not in_workspace(user, found.account_id):
            raise HTTPException(404, f"no surveillance alert {alert_id}")  # another firm's: none
        try:
            done = platform.surveillance.acknowledge(alert_id, user.id, body.note)
            return surveillance_to_dict(done)
        except KeyError:
            raise HTTPException(404, f"no surveillance alert {alert_id}") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/accounts/{account_id}/audit-export", response_model=None)
    async def audit_export(account_id: str, day: str, user: Me) -> Response:
        """One account's whole audit trail for one day, as CSV, for an auditor: every order
        event, fill, note and surveillance alert, in order."""
        runtime(account_id, user)
        _risk_only(user)
        audit_log = platform.event_log
        if audit_log is None:
            raise HTTPException(409, "no audit trail on this server")
        wanted = report_day(day)
        rows = [e for e in audit_log.events(account_id=account_id, limit=10**7)
                if e.ts.astimezone(IST).date() == wanted]  # fmt: skip
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["seq", "time", "kind", "client_order_id", "detail", "extra"])
        for e in rows:
            w.writerow([e.seq, e.ts.isoformat(), e.kind, e.client_order_id, e.detail,
                        json.dumps(e.extra, sort_keys=True, default=str)])  # fmt: skip
        name = f"mocktrade-audit-{account_id}-{wanted.isoformat()}.csv"
        disposition = {"Content-Disposition": f'attachment; filename="{name}"'}
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers=disposition)  # fmt: skip

    # --- trades made outside MockTrade (#355) -----------------------------------------------------

    def _external(account_id: str, user: User):
        running = runtime(account_id, user)
        if not _risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can resolve trades made outside "
                                "MockTrade")  # fmt: skip
        if running.external is None:
            raise HTTPException(409, "this account's broker gives no trade list to resolve from")
        return running.external

    def _product(text: str) -> Product:
        try:
            return Product(text)
        except ValueError:
            raise HTTPException(422, f"no product {text!r}") from None

    def _proposal(p) -> dict[str, Any]:
        return {"instrument_id": p.instrument_id, "product": p.product.value, "ours": p.ours,
                "broker": p.broker, "explains": p.explains, "words": p.words,
                "trades": [trade_to_dict(t) for t in p.trades]}  # fmt: skip

    @app.get("/api/accounts/{account_id}/external-trades")
    async def preview_external(account_id: str, instrument_id: str, product: str,
                               user: Me) -> dict[str, Any]:  # fmt: skip
        """What adopting would do: the broker's trades made outside MockTrade in this position,
        and whether they explain the difference (positions/external.py). Changes nothing."""
        tool = _external(account_id, user)
        try:
            return _proposal(await tool.preview(instrument_id, _product(product)))
        except BrokerError as exc:
            raise HTTPException(502, f"the broker didn't answer: {exc.reason}") from None

    @app.post("/api/accounts/{account_id}/external-trades")
    async def adopt_external(account_id: str, body: AdoptIn, user: Me) -> dict[str, Any]:
        """Adopt them, with words saying what was checked; refused unless they explain the
        difference exactly. Each adopted trade is audited (EXTERNAL_ADOPTED)."""
        tool = _external(account_id, user)
        try:
            done = await tool.adopt(body.instrument_id, _product(body.product), user.id,
                                    body.acknowledgement)  # fmt: skip
        except ExternalError as exc:
            raise HTTPException(409, str(exc)) from None
        except BrokerError as exc:
            raise HTTPException(502, f"the broker didn't answer: {exc.reason}") from None
        return {**_proposal(done), "adopted": True}

    # --- kill switches -------------------------------------------------------------------------

    @app.post("/api/accounts/{account_id}/stop")
    async def stop_account(account_id: str, body: StopIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        account = users.get_account(account_id)  # now (an admin may have changed traders)
        if not ((account and can_manage(user, account)) or _risk_or_admin(user)):
            raise HTTPException(403, "you can't stop trading on this account")
        before = _stop_snapshot([running])
        failures = await stop_trading(running.switch, [running.orders], body.reason, by=user.id)
        reports = _stop_reports([running], before, failures, user.id, body.reason)
        return {"stopped": True, "failed_cancels": _failures(failures), "reports": reports}

    @app.post("/api/accounts/{account_id}/resume")
    async def resume_account(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        owner = user.id == running.account.owner_user_id and user.has_role(Role.TRADER)
        if not (owner or _risk_or_admin(user)):
            raise HTTPException(403, "only the account owner, risk or an admin can resume")
        running.switch.reset(by=user.id)
        return {"stopped": False}

    @app.post("/api/firm/stop")
    async def stop_firm(body: StopIn, user: Me) -> dict[str, Any]:
        if not _risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can stop the whole firm")
        # the firm is the asker's workspace: its accounts and its switch, nobody else's (#368)
        runs = [r for r in platform.accounts.values() if r.account.workspace == user.workspace]
        before = _stop_snapshot(runs)
        managers = [r.orders for r in runs]
        firm = platform.firm_switch_of(user.workspace)
        failures = await stop_trading(firm, managers, body.reason, by=user.id)
        reports = _stop_reports(runs, before, failures, user.id, body.reason)
        return {"stopped": True, "failed_cancels": _failures(failures), "reports": reports}

    @app.post("/api/firm/resume")
    async def resume_firm(user: Me, request: Request) -> dict[str, Any]:
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can resume the whole firm")
        require_step_up(request, "resume the whole firm")
        platform.firm_switch_of(user.workspace).reset(by=user.id)
        return {"stopped": False}

    # --- the broker's own daily login ------------------------------------------------------------

    @app.get("/api/accounts/{account_id}/broker-login")
    async def broker_login_url(account_id: str, user: Me) -> dict[str, str]:
        running = runtime(account_id, user, trade=True)
        if broker_login is None:
            raise HTTPException(404, "this account's broker has no login step")
        if user.id != running.account.owner_user_id:
            raise HTTPException(403, "only the account holder can log in to the broker")
        now = monotonic()
        for state, (_, _, expiry) in list(login_states.items()):
            if expiry < now:
                del login_states[state]
        state = security.new_state()
        login_states[state] = (user.id, account_id, now + STATE_TTL_S)
        return {"url": broker_login.login_url(running.account, state)}

    @app.get("/kite/callback")
    async def broker_login_done(request: Request) -> RedirectResponse:
        params = request.query_params
        entry = login_states.pop(params.get("state", ""), None)
        user = users.session_user(request.cookies.get(settings.cookie_name))
        ok = (
            broker_login is not None
            and entry is not None
            and entry[2] >= monotonic()
            and user is not None
            and user.id == entry[0]
            and params.get("status") == "success"
            and bool(params.get("request_token"))
        )
        if not ok:
            return RedirectResponse("/?broker_login=failed", status_code=303)
        account = users.get_account(entry[1])
        try:
            await broker_login.complete(account, params["request_token"])
        except Exception as exc:
            log.warning("broker login for %s failed: %r", entry[1], exc)
            audit("BROKER_LOGIN_FAILED", user.id, {"error": repr(exc)}, entry[1])
            return RedirectResponse("/?broker_login=failed", status_code=303)
        audit("BROKER_LOGIN_OK", user.id, {}, entry[1])
        return RedirectResponse("/?broker_login=ok", status_code=303)

    # --- the screens (the built UI), last so they never shadow the API -------------------------

    if static_dir is not None:
        root = static_dir.resolve()
        index = root / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def screens(path: str) -> FileResponse:
            if path.startswith(("api/", "ws", "kite/")):
                raise HTTPException(404, "not found")
            target = (root / path).resolve()
            if path and target.is_file() and target.is_relative_to(root):
                if path == "popout.html":
                    # A popped-out widget's window: the main window copies its styles in as
                    # <style> elements, so inline styles must be allowed here. No script runs
                    # in it (the main window draws into it), so scripts stay forbidden.
                    return FileResponse(target, headers={"Content-Security-Policy": POPOUT_CSP})
                return FileResponse(target)
            return FileResponse(index)  # the app handles its own routes

    return app


# --- helpers ---------------------------------------------------------------------------------


def _headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _instrument(i: Instrument, expiries: Expiries | None = None) -> dict[str, Any]:
    """An instrument as JSON; with `expiries`, its expiry facts now as well (#290)."""
    return {
        "id": i.id,
        "exchange": i.exchange.value,
        "symbol": i.symbol,
        "kind": i.kind.value,
        "tick_paise": i.tick_paise,
        "lot_size": i.lot_size,
        "expiry": i.expiry.isoformat() if i.expiry else None,
        "strike_paise": i.strike_paise,
        "underlying": i.underlying,
        "tradable": i.tradable,
        "expiry_info": expiry_to_dict(expiries.info(i)) if expiries else None,
    }


def _spread_instrument(s: SpreadDef, lookup: Callable[[str], Instrument | None]) -> dict[str, Any]:
    """A spread as an instrument, for the screens: one spread lot is one "lot" (#414)."""
    return {
        "id": s.instrument_id,
        "exchange": "SPREAD",
        "symbol": s.name,
        "kind": "SPREAD",
        "tick_paise": spread_tick(s.legs, lookup),
        "lot_size": 1,
        "expiry": None,
        "strike_paise": None,
        "underlying": None,
        "tradable": True,  # a spread ladder's click starts the ASE algo (#416)
        "expiry_info": None,
        "spread": {"spread_id": s.spread_id, "legs": [leg_to_dict(x) for x in s.legs]},
    }


def _risk_or_admin(user: User) -> bool:
    return user.has_role(Role.RISK) or user.has_role(Role.ADMIN)


def _event(e: StoredEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "ts": e.ts.isoformat(),
        "account_id": e.account_id,
        "kind": e.kind,
        "client_order_id": e.client_order_id,
        "detail": e.detail,
        "order": view_to_dict(e.order) if e.order else None,
        "fill": fill_to_dict(e.fill) if e.fill else None,
        "extra": e.extra,
    }


def _me(
    user: User,
    users: UserStore,
    platform: Platform,
    broker_status: Callable[[BrokerAccount], str | None] | None = None,
) -> dict[str, Any]:
    accounts = []
    for account in users.accounts():
        if not can_view(user, account):
            continue
        running = platform.account(account.id)
        state = running.switch.state if running else None
        accounts.append(
            {
                "id": account.id,
                "broker": account.broker,
                "broker_client_id": account.broker_client_id,
                "can_trade": can_manage(user, account),  # a turned-off account: closing only
                "active": account.active,
                "running": running is not None,
                "stopped": bool(state and state.engaged),
                "stop_reason": state.reason if state and state.engaged else "",
                # "live", "login_needed" (the broker's daily login), or None (no such step)
                "broker_session": broker_status(account) if broker_status else None,
            }
        )
    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "roles": sorted(r.value for r in user.roles),
            "must_change_password": user.must_change_password,
        },
        "accounts": accounts,
        "firm_stopped": platform.firm_switch_of(user.workspace).engaged,
    }


def _stop_snapshot(runs) -> dict[str, tuple[list, int]]:
    """Before a STOP: each account's working orders and running algos, for its report."""
    return {
        r.account.id: (r.orders.working(), sum(not a.state.is_final for a in r.algos.algos()))
        for r in runs
    }


def _stop_reports(runs, before, failures, who: str, reason: str) -> list[dict[str, Any]]:
    """What the STOP did on each account, in words (risk/flatten.py), kept for the screens."""
    reports = []
    for r in runs:
        if r.flatten is None:
            continue
        working, algos = before[r.account.id]
        report = r.flatten.stop_report(working, failures.get(r.account.id, []), algos, who,
                                       reason)  # fmt: skip
        reports.append({"account_id": r.account.id, **report})
    return reports


def _failures(failures: dict[str, list[tuple[str, str]]]) -> list[dict[str, str]]:
    return [
        {"account_id": account_id, "client_order_id": cid, "reason": reason}
        for account_id, items in failures.items()
        for cid, reason in items
    ]
