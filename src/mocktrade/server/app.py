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
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from mocktrade.domain.accounts import BrokerAccount, Role, User, can_trade, can_view
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side, Validity
from mocktrade.orders.codec import fill_to_dict, view_to_dict
from mocktrade.orders.model import OrderError
from mocktrade.platform import AccountRuntime, Platform
from mocktrade.positions.book import PositionView
from mocktrade.risk.kill_switch import stop_trading
from mocktrade.server import security
from mocktrade.server.users import SESSION_MAX, LoginFailed, UserStore

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOGIN_LIMIT_PER_IP = 20  # attempts per 15 minutes from one address
LOGIN_WINDOW_S = 15 * 60
STATE_TTL_S = 10 * 60

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


class StopIn(_Strict):
    reason: str = Field(min_length=1, max_length=200)


# --- the app -------------------------------------------------------------------------------


def create_app(
    platform: Platform,
    users: UserStore,
    settings: ServerSettings,
    *,
    broker_login: BrokerLogin | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="MockTrade", docs_url=None, redoc_url=None, openapi_url=None)
    login_attempts: dict[str, deque[float]] = {}
    login_states: dict[str, tuple[str, str, float]] = {}  # state -> (user, account, expiry)

    # --- every request ------------------------------------------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get("origin") != settings.origin:
                return _headers(JSONResponse({"detail": "request from another site"}, 403))
            if not request.headers.get("content-type", "").startswith("application/json"):
                if request.method != "DELETE":
                    return _headers(JSONResponse({"detail": "send JSON"}, 415))
        return _headers(await call_next(request))

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
        return _me(user, users, platform)

    @app.delete("/api/session", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        users.end_session(request.cookies.get(settings.cookie_name))
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.cookie_secure)

    @app.get("/api/me")
    async def me(user: Me) -> dict[str, Any]:
        return _me(user, users, platform)

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
        return [
            {
                "seq": e.seq,
                "ts": e.ts.isoformat(),
                "kind": e.kind,
                "detail": e.detail,
                "order": view_to_dict(e.order) if e.order else None,
                "fill": fill_to_dict(e.fill) if e.fill else None,
            }
            for e in events
            if e.account_id == account_id
        ]

    # --- positions -----------------------------------------------------------------------------

    @app.get("/api/accounts/{account_id}/positions")
    async def positions(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        book = running.positions
        last = running.reconciler.last
        return {
            "positions": [_position(p) for p in book.positions()],
            "realised_paise": book.realised_paise(),
            "unrealised_paise": book.unrealised_paise(),
            "day_pnl_paise": book.day_pnl_paise(),
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

    return app


# --- helpers ---------------------------------------------------------------------------------


def _headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _risk_or_admin(user: User) -> bool:
    return user.has_role(Role.RISK) or user.has_role(Role.ADMIN)


def _me(user: User, users: UserStore, platform: Platform) -> dict[str, Any]:
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
            }
        )
    return {
        "user": {"id": user.id, "name": user.name, "roles": sorted(r.value for r in user.roles)},
        "accounts": accounts,
        "firm_stopped": platform.firm_switch.engaged,
    }


def _position(p: PositionView) -> dict[str, Any]:
    return {
        "instrument_id": p.instrument_id,
        "product": p.product.value,
        "qty": p.qty,
        "avg_price_paise": _decimal(p.avg_price_paise),
        "ltp_paise": p.ltp_paise,
        "realised_paise": p.realised_paise,
        "unrealised_paise": p.unrealised_paise,
        "pnl_paise": p.pnl_paise,
        "buy_qty": p.buy_qty,
        "sell_qty": p.sell_qty,
        "buy_value_paise": p.buy_value_paise,
        "sell_value_paise": p.sell_value_paise,
    }


def _failures(failures: dict[str, list[tuple[str, str]]]) -> list[dict[str, str]]:
    return [
        {"account_id": account_id, "client_order_id": cid, "reason": reason}
        for account_id, items in failures.items()
        for cid, reason in items
    ]


def _decimal(value: Fraction) -> str:
    """An exact average like 10007.5 paise as text, at most 4 decimals (1/3 can't be exact)."""
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return f"{exact.quantize(Decimal('0.0001')).normalize():f}"  # never "1E+4"
