"""The HTTP API: login, accounts, orders, positions, kill switches, audit history.

    create_app ─► the shared parts (prices per subscription, spreads, the status, the stream)
               ─► ServerContext: the server's services and the checks every route uses
               ─► one router per area (server/routes/), then the admin API (admin.py)
               ─► the built screens, last, so they never shadow the API

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

import functools
import time
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from mocktrade.alerts.service import MarketAlerts
from mocktrade.charts.service import Charts
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.marketdata.markets import Markets
from mocktrade.marketdata.service import MarketData
from mocktrade.metrics import METRICS, process_gauges, since_us
from mocktrade.ops import logs
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.protection import ProtectionGuard
from mocktrade.ops.scheduler import Scheduler
from mocktrade.ops.status import Status
from mocktrade.platform import Platform
from mocktrade.reports.service import Reports
from mocktrade.research.backtest import Backtests, CandleStore
from mocktrade.research.replay import ReplayFeed
from mocktrade.research.ticks import FileTickStore
from mocktrade.server.admin import AccountStarter, admin_router
from mocktrade.server.context import BrokerLogin, ServerContext, ServerSettings
from mocktrade.server.routes import (
    market,
    ops,
    portfolio,
    research,
    risk,
    session,
    spreads,
    trading,
)
from mocktrade.server.stream import StreamHub
from mocktrade.server.users import UserStore
from mocktrade.spreads.pricer import SpreadPricer
from mocktrade.spreads.store import SpreadBook

__all__ = ["BrokerLogin", "ServerSettings", "create_app"]  # where the assembly imports them

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

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

# the order the areas' routes are tried in: none of them can shadow another's (a test checks)
AREAS = (ops, session, market, research, trading, spreads, risk, portfolio)


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
    status = Status(platform.supervisor, lambda: platform.accounts, alerts,
                    lambda: platform.event_log.problem if platform.event_log else None)  # fmt: skip
    app.state.status = status  # its watch() runs with the server's other jobs
    ctx = ServerContext(
        platform, users, settings, markets, no_prices, book, status,
        broker_login=broker_login, broker_status=broker_status, alerts=alerts,
        scheduler=scheduler, reports=reports, replay_command=replay_command, replay=replay,
        backtests=backtests, candles=candles, candles_for=candles_for,
        market_alerts=market_alerts, protection=protection, monotonic=monotonic,
    )  # fmt: skip

    hub = StreamHub(
        platform,
        users,
        origin=settings.origin,
        cookie_name=settings.cookie_name,
        alerts=alerts,
        market=ctx.market,
        market_alerts=market_alerts,
        spread_visible=lambda user, i: book.visible(user, i) is not None,
        spread_of=book.visible,
    )
    app.state.hub = hub
    app.add_api_websocket_route("/ws", hub.serve)
    _gauges(platform, hub, status, alerts)

    # --- every request ------------------------------------------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        started = time.perf_counter_ns()
        request_id = logs.start_request(request.headers.get("x-request-id"))  # #287
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get("origin") != settings.origin:
                return _headers(JSONResponse({"detail": "request from another site"}, 403))
            if not request.headers.get("content-type", "").startswith("application/json"):
                if request.method != "DELETE":
                    return _headers(JSONResponse({"detail": "send JSON"}, 415))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id  # quoted in a report: finds its log lines
        if request.url.path.startswith("/api/"):
            route = request.scope.get("route")
            name = getattr(route, "path", "other")  # the template: no ids in metric names
            METRICS.observe(f"http_us{{route={request.method} {name}}}", since_us(started))
            METRICS.inc(f"http_responses{{status={response.status_code // 100}xx}}")
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            # built files carry a content hash in their name: safe to cache for a year
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return _headers(response)

    # --- the API, area by area ---------------------------------------------------------------

    for area in AREAS:
        app.include_router(area.router(ctx))
    app.include_router(admin_router(platform, users, ctx.current_user, ctx.audit, starter,
                                    ctx.require_step_up))  # fmt: skip

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


def _gauges(platform: Platform, hub: StreamHub, status: Status, alerts: Alerts | None) -> None:
    """What the metrics dashboard's top rows read (#287)."""
    METRICS.gauge("stream_connections", lambda: hub.connections)
    METRICS.gauge("ready", lambda: 1 if status.ready()[0] else 0)

    def active_alerts(level: str) -> int:
        return sum(a.level == level for a in alerts.active()) if alerts else 0

    for level in ("WARN", "ERROR"):
        METRICS.gauge(f"ops_alerts{{level={level}}}", functools.partial(active_alerts, level))
    sources = platform.prices.all() or [platform.market_data]  # every subscription's (#396)
    METRICS.gauge("ticks_received", lambda: sum(md.ticks_received for md in sources))
    METRICS.gauge("instruments_watched", lambda: sum(len(md.watched()) for md in sources))
    process_gauges(METRICS)


def _headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
