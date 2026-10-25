"""Health, readiness, the status page, metrics, operations and a replay's controls (#287).

GET    /api/health      alive? (no login): the version the screens show
GET    /api/ready       ready to trade? (no login): yes, or how many parts are in the way
GET    /api/status      each part, its state, why and since when (admin and risk)
GET    /api/metrics     how fast each step is; ?format=prometheus for a monitoring system
DELETE /api/metrics     start measuring afresh (admin)
GET    /api/ops         active alerts and the scheduled jobs that ran (admin and risk)
GET    /api/replay      a replay server's clock; POST: pause, resume, speed, seek
"""

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from laddrix.domain.accounts import Role, User
from laddrix.metrics import METRICS
from laddrix.ops.alerts import alert_to_dict
from laddrix.ops.status import NOT_READY, worst
from laddrix.server.bodies import ReplayIn
from laddrix.server.context import ServerContext, risk_or_admin
from laddrix.version import BUILD, VERSION


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    users = ctx.users
    settings = ctx.settings
    status = ctx.status
    alerts = ctx.alerts
    scheduler = ctx.scheduler
    replay = ctx.replay
    in_workspace = ctx.in_workspace
    platform_wide = ctx.platform_wide
    audit = ctx.audit
    current_user = ctx.current_user
    Me = Annotated[User, Depends(ctx.current_user)]

    @api.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION, "build": BUILD}  # the screens show it

    @api.get("/api/ready", response_model=None)
    async def ready() -> Any:
        """Readiness, apart from liveness (/api/health, #273, #287): no part of the server is
        starting or down (ops/status.py). Says only yes or no and how many parts are in the
        way (it needs no login); which and why: GET /api/status, for admin and risk."""
        ok, blocking = status.ready()
        if ok:
            return {"ready": True}
        return JSONResponse({"ready": False, "problems": len(blocking)}, status_code=503)

    @api.get("/api/status")
    async def server_status(user: Me) -> dict[str, Any]:
        """The status page (#287): each part, its state, why and since when. Another firm's
        accounts are never shown (#368)."""
        if not risk_or_admin(user):
            raise HTTPException(403, "the status page is for admin and risk users")
        every = status.parts()
        parts = [p for p in every if p.account_id is None or in_workspace(user, p.account_id)]
        hidden = sum(p.state in NOT_READY for p in every) - sum(p.state in NOT_READY for p in parts)
        return {
            "state": worst(parts),
            "ready": hidden == 0 and all(p.state not in NOT_READY for p in parts),
            "other_firms_not_ready": hidden,  # the server is shared: say so, never whose
            "at": datetime.now(UTC).isoformat(),
            "parts": [p.to_dict() for p in parts],
        }

    def scraper(request: Request) -> bool:
        """The monitoring system's own key (#287), compared in constant time."""
        given = request.headers.get("authorization", "")
        token = settings.metrics_token
        return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

    @api.get("/api/metrics", response_model=None)
    async def metrics(request: Request, format: str = "json") -> dict[str, Any] | Response:
        """How fast each step is (metrics.py). Admin and risk, or the monitoring system's key
        (the Prometheus format only: it reads nothing else)."""
        if not (format == "prometheus" and scraper(request)):
            user = current_user(request)
            if not risk_or_admin(user):
                raise HTTPException(403, "metrics are for admin and risk users")
        if format == "prometheus":
            return Response(METRICS.prometheus(), media_type="text/plain; version=0.0.4")
        return METRICS.snapshot()

    @api.get("/api/ops")
    async def ops(user: Me) -> dict[str, Any]:
        """What operations look like now: active alerts and the scheduled jobs that ran."""
        if not risk_or_admin(user):
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

    @api.delete("/api/metrics")
    async def reset_metrics(user: Me) -> dict[str, str]:
        """Start measuring afresh (e.g. after a load test's warm-up). Admin only."""
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can reset metrics")
        platform_wide(user, "reset the metrics")
        METRICS.reset()
        audit("METRICS_RESET", user.id, {})
        return {"status": "reset"}

    # --- a replay server's controls (K11.2) ------------------------------------------------------

    @api.get("/api/replay")
    async def replay_status(user: Me) -> dict[str, Any]:
        if replay is None:
            raise HTTPException(404, "this server isn't replaying a recorded day")
        return replay.status()

    @api.post("/api/replay")
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

    return api
