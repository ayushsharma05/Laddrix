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
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from mocktrade.algos.engine import AlgoError
from mocktrade.algos.spec import spec_to_dict as algo_spec_to_dict
from mocktrade.domain.accounts import BrokerAccount, Role, User, can_trade, can_view
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
from mocktrade.orders.model import OrderError
from mocktrade.platform import AccountRuntime, Platform, spec_of
from mocktrade.risk.kill_switch import stop_trading
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
