"""Logging in and out, the user's own password, step-up, sessions, layouts and settings, and
the broker's own daily login (Kite's).

    POST   /api/session                    log in (password and authenticator code)
    DELETE /api/session                    log out
    GET    /api/me                         who am I, and the accounts I see
    POST   /api/me/password                change my password (also after an admin's reset)
    POST   /api/me/step-up                 a fresh authenticator code for a privileged change
    GET    /api/me/sessions                my sessions; DELETE …/{id} ends one
    GET    /api/layouts[/{name}]           my saved screen layouts; PUT saves, DELETE forgets
    GET    /api/me/settings/{name}         my shortcuts, theme…; PUT saves (null: the defaults)
    GET    /api/accounts/{id}/broker-login the broker's login page, for the account holder
    GET    /kite/callback                  where the broker sends them back
"""

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from mocktrade.domain.accounts import BrokerAccount, User, can_manage, can_view
from mocktrade.platform import Platform
from mocktrade.server import security
from mocktrade.server.bodies import CodeIn, LayoutIn, LoginIn, PasswordIn, SettingIn
from mocktrade.server.context import ServerContext
from mocktrade.server.users import SESSION_MAX, LoginFailed, UserStore

log = logging.getLogger(__name__)

LOGIN_LIMIT_PER_IP = 20  # attempts per 15 minutes from one address
LOGIN_WINDOW_S = 15 * 60
STATE_TTL_S = 10 * 60


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    users = ctx.users
    settings = ctx.settings
    broker_login = ctx.broker_login
    broker_status = ctx.broker_status
    monotonic = ctx.monotonic
    login_attempts = ctx.login_attempts
    login_states = ctx.login_states
    runtime = ctx.runtime
    audit = ctx.audit
    Me = Annotated[User, Depends(ctx.current_user)]

    @api.post("/api/session")
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
        return me_to_dict(user, users, platform, broker_status)

    @api.delete("/api/session", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        users.end_session(request.cookies.get(settings.cookie_name))
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.cookie_secure)

    @api.get("/api/me")
    async def me(user: Me) -> dict[str, Any]:
        return me_to_dict(user, users, platform, broker_status)

    @api.post("/api/me/password", status_code=204)
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

    @api.post("/api/me/step-up", status_code=204)
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

    @api.get("/api/me/sessions")
    async def my_sessions(user: Me, request: Request) -> list[dict[str, Any]]:
        return users.own_sessions(user.id, request.cookies.get(settings.cookie_name))

    @api.delete("/api/me/sessions/{session_id}", status_code=204)
    async def end_my_session(session_id: str, user: Me) -> None:
        if not users.end_own_session(user.id, session_id):
            raise HTTPException(404, "no such session")  # someone else's is the same answer
        audit("SESSION_ENDED", f"{user.id} ended one of their sessions",
              {"user_id": user.id, "session": session_id})  # fmt: skip

    @api.get("/api/layouts")
    async def list_layouts(user: Me) -> list[dict[str, str]]:
        return [{"name": n, "updated_at": t} for n, t in users.layouts(user.id)]

    @api.get("/api/layouts/{name}")
    async def get_layout(name: str, user: Me) -> dict[str, Any]:
        layout = users.get_layout(user.id, name)
        if layout is None:
            raise HTTPException(404, "no such layout")
        return {"name": name, "layout": json.loads(layout)}

    @api.put("/api/layouts/{name}")
    async def save_layout(name: str, body: LayoutIn, user: Me) -> dict[str, str]:
        try:
            users.save_layout(user.id, name, json.dumps(body.layout, separators=(",", ":")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"name": name}

    @api.delete("/api/layouts/{name}", status_code=204)
    async def delete_layout(name: str, user: Me) -> None:
        if not users.delete_layout(user.id, name):
            raise HTTPException(404, "no such layout")

    # --- the user's own screen settings: shortcuts (K13.4), theme (K13.5) -----------------------

    @api.get("/api/me/settings/{name}")
    async def get_setting(name: str, user: Me) -> dict[str, Any]:
        """The value as the screens saved it, or null: nothing saved (the defaults)."""
        try:
            value = users.get_setting(user.id, name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"name": name, "value": json.loads(value) if value is not None else None}

    @api.put("/api/me/settings/{name}")
    async def put_setting(name: str, body: SettingIn, user: Me) -> dict[str, Any]:
        """Kept as given (the screens own its shape); null forgets it: back to the defaults."""
        value = None if body.value is None else json.dumps(body.value, separators=(",", ":"))
        try:
            users.set_setting(user.id, name, value)
        except ValueError as exc:
            code = 404 if "no setting" in str(exc) else 422
            raise HTTPException(code, str(exc)) from None
        return {"name": name, "value": body.value}

    # --- the broker's own daily login ------------------------------------------------------------

    @api.get("/api/accounts/{account_id}/broker-login")
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

    @api.get("/kite/callback")
    async def broker_login_done(request: Request) -> RedirectResponse:
        params = request.query_params
        entry = login_states.pop(params.get("state", ""), None)
        user = users.session_user(request.cookies.get(settings.cookie_name))
        failed = RedirectResponse("/?broker_login=failed", status_code=303)
        if broker_login is None or entry is None or user is None:
            return failed
        account = users.get_account(entry[1])  # gone meanwhile (an admin removed it): failed
        token = params.get("request_token")
        if (entry[2] < monotonic() or user.id != entry[0] or account is None
                or params.get("status") != "success" or not token):  # fmt: skip
            return failed
        try:
            await broker_login.complete(account, params["request_token"])
        except Exception as exc:
            log.warning("broker login for %s failed: %r", entry[1], exc)
            audit("BROKER_LOGIN_FAILED", user.id, {"error": repr(exc)}, entry[1])
            return RedirectResponse("/?broker_login=failed", status_code=303)
        audit("BROKER_LOGIN_OK", user.id, {}, entry[1])
        return RedirectResponse("/?broker_login=ok", status_code=303)

    return api


def me_to_dict(
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
