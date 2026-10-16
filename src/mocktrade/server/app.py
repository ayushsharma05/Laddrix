"""The HTTP API: login, accounts, orders, positions, kill switches, audit history.

Every /api route except login and health needs a session cookie. Rules that hold
for every request:
- Who you are comes from the session, never from the request body.
- Account routes check can_view / can_trade for that account.
- Changing requests (POST, PATCH, PUT, DELETE) must come from our own page: the
  Origin header must match, the cookie is SameSite=Strict, and bodies are JSON.
- Responses carry security headers (no framing, strict content policy, HSTS).

Money is whole paise in and out (the screens format it); order objects use the
same format as the event log (orders/codec.py).
"""

import asyncio
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

from mocktrade.algos.engine import AlgoError
from mocktrade.algos.spec import spec_to_dict as algo_spec_to_dict
from mocktrade.charges.model import bill_to_dict
from mocktrade.charges.schedules import schedule_to_dict
from mocktrade.charges.service import ChargesError
from mocktrade.charts.candles import IST
from mocktrade.charts.service import ChartError, Charts
from mocktrade.domain.accounts import BrokerAccount, Role, User, can_trade, can_view
from mocktrade.domain.gtt import gtt_spec_to_dict, gtt_to_dict
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.order_spec import spec_to_dict
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side, Validity
from mocktrade.metrics import METRICS, process_gauges, since_us
from mocktrade.ops.alerts import Alerts, alert_to_dict
from mocktrade.ops.scheduler import Scheduler
from mocktrade.options.chain import ChainService
from mocktrade.options.strategy import Strategies, StrategyError
from mocktrade.options.strategy import templates as strategy_templates_list
from mocktrade.orders.basket import BasketError, LegIn, check_to_dict
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.orders.gtt import GttError, gtt_request_from
from mocktrade.orders.model import OrderError
from mocktrade.platform import AccountRuntime, Platform, spec_of
from mocktrade.portfolio.service import PortfolioError, snapshot_to_dict
from mocktrade.reports.export import report_csv, report_pdf
from mocktrade.reports.service import ReportError, Reports
from mocktrade.research.backtest import (
    MAX_DAYS,
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
from mocktrade.server import security
from mocktrade.server.formats import algo_to_dict, position_to_dict
from mocktrade.server.stream import StreamHub
from mocktrade.server.users import SESSION_MAX, LoginFailed, UserStore
from mocktrade.store.events import StoredEvent

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


class CandlesIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    day: date
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class ConfirmIn(_Strict):
    confirm: bool


class StopIn(_Strict):
    reason: str = Field(min_length=1, max_length=200)


class LayoutIn(_Strict):
    layout: dict[str, Any]  # the screens' own description of their widgets


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
    charts: Charts | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="MockTrade", docs_url=None, redoc_url=None, openapi_url=None)
    chains = ChainService(platform.instruments, platform.market_data)
    strategies = Strategies(chains)
    hub = StreamHub(
        platform,
        users,
        origin=settings.origin,
        cookie_name=settings.cookie_name,
        alerts=alerts,
        chains=chains,
        charts=charts,
    )
    app.state.hub = hub
    METRICS.gauge("stream_connections", lambda: hub.connections)
    METRICS.gauge("ticks_received", lambda: platform.market_data.ticks_received)
    METRICS.gauge("instruments_watched", lambda: len(platform.market_data.watched()))
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
        return user

    Me = Annotated[User, Depends(current_user)]

    def runtime(account_id: str, user: User, *, trade: bool = False) -> AccountRuntime:
        account = users.get_account(account_id)
        running = platform.account(account_id)
        if account is None or running is None or not can_view(user, account):
            raise HTTPException(404, "no such account")  # don't reveal other people's accounts
        if trade and not can_trade(user, account):
            raise HTTPException(403, "you can view this account but not trade it")
        return running

    def audit(kind: str, detail: str, extra: dict[str, Any], account_id: str = "-") -> None:
        if platform.event_log is not None:
            platform.event_log.record_note(kind, account_id, detail, extra)

    # --- login ---------------------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        return chains.underlyings()

    @app.get("/api/options/{underlying}/expiries")
    async def option_expiries(underlying: str, user: Me) -> list[str]:
        return [e.isoformat() for e in chains.expiries(underlying)]

    @app.get("/api/options/{underlying}/{expiry}/chain")
    async def option_chain(
        underlying: str, expiry: str, user: Me, strikes: int = 15
    ) -> dict[str, Any]:
        try:
            day = date.fromisoformat(expiry)
        except ValueError:
            raise HTTPException(422, "expiry must be a date like 2026-10-27") from None
        if day not in chains.expiries(underlying):
            raise HTTPException(404, f"no options on {underlying} expiring {expiry}")
        return chains.snapshot(underlying, day, max(1, min(strikes, 40)))

    @app.get("/api/options/strategies")
    async def strategy_templates(user: Me) -> list[dict[str, Any]]:
        return strategy_templates_list()

    @app.post("/api/options/strategy/build")
    async def strategy_build(body: StrategyBuildIn, user: Me) -> dict[str, Any]:
        try:
            legs = strategies.build(body.code, body.underlying, body.expiry, body.width, body.lots)
            return strategies.analyse(legs)
        except StrategyError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/options/strategy/analyse")
    async def strategy_analyse(body: StrategyIn, user: Me) -> dict[str, Any]:
        legs = [{**leg.model_dump(exclude_none=True), "side": leg.side.value} for leg in body.legs]
        try:
            return strategies.analyse(legs)
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

    def recordings():
        if ticks is None:
            raise HTTPException(404, "ticks aren't being recorded on this server")
        return ticks

    @app.get("/api/research/recordings")
    async def recorded_days(user: Me) -> dict[str, Any]:
        store = recordings()
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
        store = recordings()
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, f"not a date: {day!r} (YYYY-MM-DD)") from None
        if when not in store.days():
            raise HTTPException(404, f"nothing recorded on {day}")
        return await asyncio.to_thread(store.summary, when)

    @app.delete("/api/research/recordings/{day}")
    async def recorded_day_delete(day: str, user: Me) -> dict[str, Any]:
        store = recordings()
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can delete recordings")
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
            run = runs.start(spec, candles=body.candles)
        except BacktestError as exc:
            raise HTTPException(422, str(exc)) from None
        audit("BACKTEST", f"{user.id} started {run.run_id}: {body.algo} {body.side.value} "
              f"{body.qty} {body.instrument_id} over {len(body.days)} days", {})  # fmt: skip
        return run.to_dict(full=False)

    @app.get("/api/research/backtests")
    async def backtest_list(user: Me) -> list[dict[str, Any]]:
        return [r.to_dict(full=False) for r in backtest_runs().runs()]

    @app.get("/api/research/backtests/form")
    async def backtest_form_spec(user: Me, instrument_id: str = "") -> dict[str, Any]:
        backtest_runs()
        days = await asyncio.to_thread(ticks.days) if ticks is not None else []
        out = form_spec(days, candles=candles is not None)
        if candles is not None and instrument_id:
            out["candle_days"] = await asyncio.to_thread(candles.days, instrument_id)
        return out

    @app.get("/api/research/backtests/{run_id}/days/{day}", response_model=None)
    async def backtest_day(run_id: str, day: str, user: Me, format: str = "json") -> Any:
        if format not in ("json", "csv", "pdf"):
            raise HTTPException(422, "format must be json, csv or pdf")
        try:
            data = backtest_runs().day(run_id, day)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        if format == "json":
            return data
        name = f"mocktrade-backtest-{run_id}-{data['day']}"
        disposition = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
        if format == "csv":
            return Response(report_csv(data), media_type="text/csv; charset=utf-8",
                            headers=disposition)  # fmt: skip
        return Response(report_pdf(data), media_type="application/pdf", headers=disposition)

    @app.get("/api/research/backtests/{run_id}")
    async def backtest_get(run_id: str, user: Me) -> dict[str, Any]:
        run = backtest_runs().get(run_id)
        if run is None:
            raise HTTPException(404, f"no backtest {run_id}")
        return run.to_dict()

    @app.post("/api/research/candles", status_code=201)
    async def candles_import(body: CandlesIn, user: Me) -> dict[str, Any]:
        if candles is None:
            raise HTTPException(404, "candles can't be imported on this server")
        try:
            n = candles.put(body.instrument_id, body.day, body.rows)
        except BacktestError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"instrument_id": body.instrument_id, "day": body.day.isoformat(), "rows": n}

    # --- charts (K12.1) ---------------------------------------------------------------------

    def chart_service() -> Charts:
        if charts is None:
            raise HTTPException(404, "charts aren't set up on this server")
        return charts

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
        return chart_service().spec()

    @app.get("/api/charts/{instrument_id}/candles")
    async def chart_candles(instrument_id: str, user: Me, interval: str = "1m", start: str = "",
                            end: str = "") -> dict[str, Any]:  # fmt: skip
        service = chart_service()
        first, last = chart_time(start, "start"), chart_time(end, "end")
        try:
            return await service.candles(chart_instrument(instrument_id), interval, first, last)
        except ChartError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/accounts/{account_id}/charts/{instrument_id}/marks")
    async def chart_marks(account_id: str, instrument_id: str, user: Me, start: str = "",
                          end: str = "") -> dict[str, Any]:  # fmt: skip
        runtime(account_id, user)
        service = chart_service()
        last = chart_time(end, "end") or datetime.now(IST) + timedelta(minutes=1)
        first = chart_time(start, "start") or last - timedelta(days=1)
        marks = await asyncio.to_thread(service.marks, account_id, chart_instrument(instrument_id),
                                        first, last)  # fmt: skip
        return {"account_id": account_id, "instrument_id": instrument_id, "marks": marks}

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
        try:
            data = day_reports.day(account_id, report_day(day))
        except ReportError as exc:
            raise HTTPException(422, str(exc)) from None
        if format == "json":
            return data
        name = f"mocktrade-{account_id}-{data['day']}{'' if data['final'] else '-preview'}"
        disposition = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
        if format == "csv":
            return Response(report_csv(data), media_type="text/csv; charset=utf-8",
                            headers=disposition)  # fmt: skip
        return Response(report_pdf(data), media_type="application/pdf", headers=disposition)

    @app.get("/api/accounts/{account_id}/pnl")
    async def pnl(account_id: str, user: Me, by: str = "day", start: str = "",
                  end: str = "") -> dict[str, Any]:  # fmt: skip
        runtime(account_id, user)
        last = report_day(end) if end else day_reports.today()
        first = report_day(start) if start else last - timedelta(days=30)
        try:
            return {"account_id": account_id, **day_reports.history(account_id, by, first, last)}
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
        return {
            "alerts": [alert_to_dict(a) for a in alerts.active()] if alerts else [],
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
        token = users.start_session(user)
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

    # --- instruments and layouts -------------------------------------------------------------

    @app.get("/api/instruments")
    async def search_instruments(user: Me, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        return [_instrument(i) for i in platform.instruments.search(q, limit)]

    @app.get("/api/instruments/{instrument_id}")
    async def get_instrument(instrument_id: str, user: Me) -> dict[str, Any]:
        instrument = platform.instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "no such instrument")
        tick = platform.market_data.latest(instrument_id)
        return {**_instrument(instrument), "ltp_paise": tick.ltp_paise if tick else None}

    @app.get("/api/instruments/{instrument_id}/vap")
    async def get_vap(instrument_id: str, user: Me) -> dict[str, Any]:
        """Today's volume at each price (an estimate from snapshots, see marketdata/vap.py)."""
        if platform.instruments.get(instrument_id) is None:
            raise HTTPException(404, "no such instrument")
        volume, levels = platform.market_data.vap.snapshot(instrument_id)
        return {"volume": volume, "levels": [[p, q] for p, q in sorted(levels.items())]}

    @app.get("/api/accounts/{account_id}/instruments/{instrument_id}/order-spec")
    async def get_order_spec(account_id: str, instrument_id: str, user: Me) -> dict[str, Any]:
        """What can be ordered on this instrument through this account's broker: the order
        types, what each needs, validities, products, tick and lot (domain/order_spec.py).
        The screens draw their tickets from this; nothing broker-specific lives in the UI."""
        running = runtime(account_id, user)
        instrument = platform.instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "no such instrument")
        return spec_to_dict(spec_of(running.broker)(instrument))

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
        return view_to_dict(await running.orders.submit(request))

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
        names = [account_id, f"account {account_id}", "firm"]
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
        return [algo_spec_to_dict(s) for s in runtime(account_id, user).algos.specs()]

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
                        "ours": m.ours,
                        "broker": m.broker,
                        "since": m.since.isoformat(),
                        "message": m.message,
                    }
                    for m in running.reconciler.alerts()
                ],
            },
        }

    # --- kill switches -------------------------------------------------------------------------

    @app.post("/api/accounts/{account_id}/stop")
    async def stop_account(account_id: str, body: StopIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if not (can_trade(user, running.account) or _risk_or_admin(user)):
            raise HTTPException(403, "you can't stop trading on this account")
        failures = await stop_trading(running.switch, [running.orders], body.reason, by=user.id)
        return {"stopped": True, "failed_cancels": _failures(failures)}

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
        managers = [r.orders for r in platform.accounts.values()]
        failures = await stop_trading(platform.firm_switch, managers, body.reason, by=user.id)
        return {"stopped": True, "failed_cancels": _failures(failures)}

    @app.post("/api/firm/resume")
    async def resume_firm(user: Me) -> dict[str, Any]:
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can resume the whole firm")
        platform.firm_switch.reset(by=user.id)
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


def _instrument(i: Instrument) -> dict[str, Any]:
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
                "can_trade": can_trade(user, account),
                "running": running is not None,
                "stopped": bool(state and state.engaged),
                "stop_reason": state.reason if state and state.engaged else "",
                # "live", "login_needed" (the broker's daily login), or None (no such step)
                "broker_session": broker_status(account) if broker_status else None,
            }
        )
    return {
        "user": {"id": user.id, "name": user.name, "roles": sorted(r.value for r in user.roles)},
        "accounts": accounts,
        "firm_stopped": platform.firm_switch.engaged,
    }


def _failures(failures: dict[str, list[tuple[str, str]]]) -> list[dict[str, str]]:
    return [
        {"account_id": account_id, "client_order_id": cid, "reason": reason}
        for account_id, items in failures.items()
        for cid, reason in items
    ]
