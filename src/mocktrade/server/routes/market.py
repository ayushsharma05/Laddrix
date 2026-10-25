"""Prices and what is drawn from them: instruments, options, charts, market alerts, movers.
Each user sees their own subscription's prices (#395), never another's.

    GET /api/instruments[?q]                         search (expired contracts don't come up)
    GET /api/instruments/{id}[/vap|/prints]          one instrument, its volume at price, prints
    GET /api/accounts/{id}/instruments/{i}/order-spec what this account's broker allows on it
    GET /api/market/movers                           top gainers, losers, most active
    GET /api/options/…                               underlyings, expiries, chains, strategies
    GET /api/charts/…                                candles; an account's fills as marks
        /api/alerts/…                                each user's own price alerts and webhook
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response

from mocktrade.alerts.engine import AlertError
from mocktrade.alerts.engine import spec as alert_spec
from mocktrade.alerts.service import MarketAlerts
from mocktrade.charts.candles import IST
from mocktrade.charts.service import ChartError, Charts, spread_candles
from mocktrade.domain.accounts import User
from mocktrade.domain.order_spec import spec_to_dict
from mocktrade.marketdata.movers import MoversError
from mocktrade.marketdata.prints import print_to_dict
from mocktrade.options.strategy import Strategies, StrategyError
from mocktrade.options.strategy import templates as strategy_templates_list
from mocktrade.platform import spec_of
from mocktrade.refdata.expiry import expiry_to_dict
from mocktrade.server.bodies import AlertIn, AlertPatch, StrategyBuildIn, StrategyIn, WebhookIn
from mocktrade.server.context import ServerContext
from mocktrade.server.formats import (
    instrument_to_dict,
    spread_instrument_to_dict,
)
from mocktrade.spreads.model import (
    PREFIX,
)


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    book = ctx.book
    market_alerts = ctx.market_alerts
    market = ctx.market
    runtime = ctx.runtime
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- options -------------------------------------------------------------------------------

    @api.get("/api/options")
    async def option_underlyings(user: Me) -> list[str]:
        return market(user).chains.underlyings()

    @api.get("/api/options/{underlying}/expiries")
    async def option_expiries(underlying: str, user: Me) -> list[str]:
        return [e.isoformat() for e in market(user).chains.expiries(underlying)]

    @api.get("/api/options/{underlying}/{expiry}/chain")
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

    @api.get("/api/options/strategies")
    async def strategy_templates(user: Me) -> list[dict[str, Any]]:
        return strategy_templates_list()

    @api.post("/api/options/strategy/build")
    async def strategy_build(body: StrategyBuildIn, user: Me) -> dict[str, Any]:
        strategies = Strategies(market(user).chains)
        try:
            legs = strategies.build(body.code, body.underlying, body.expiry, body.width, body.lots)
            return strategies.analyse(legs)
        except StrategyError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.post("/api/options/strategy/analyse")
    async def strategy_analyse(body: StrategyIn, user: Me) -> dict[str, Any]:
        legs = [{**leg.model_dump(exclude_none=True), "side": leg.side.value} for leg in body.legs]
        try:
            return Strategies(market(user).chains).analyse(legs)
        except StrategyError as exc:
            raise HTTPException(422, str(exc)) from None

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

    @api.get("/api/charts/spec")
    async def chart_spec(user: Me) -> dict[str, Any]:
        return chart_service(user).spec()

    @api.get("/api/charts/{instrument_id}/candles")
    async def chart_candles(instrument_id: str, user: Me, interval: str = "1m", start: str = "",
                            end: str = "") -> dict[str, Any]:  # fmt: skip
        service = chart_service(user)
        first, last = chart_time(start, "start"), chart_time(end, "end")
        try:
            if instrument_id.startswith(PREFIX):  # a spread: from its legs' candles (#429)
                spread = book.visible(user, instrument_id)
                if spread is None:
                    raise HTTPException(404, f"unknown instrument {instrument_id}")
                return await spread_candles(service, spread, interval, first, last)
            return await service.candles(chart_instrument(instrument_id), interval, first, last)
        except ChartError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get("/api/accounts/{account_id}/charts/{instrument_id}/marks")
    async def chart_marks(account_id: str, instrument_id: str, user: Me, start: str = "",
                          end: str = "") -> dict[str, Any]:  # fmt: skip
        runtime(account_id, user)
        service = chart_service(user)
        if instrument_id.startswith(PREFIX):  # a spread (#429): its fills are on its legs
            if book.visible(user, instrument_id) is None:
                raise HTTPException(404, f"unknown instrument {instrument_id}")
            return {"account_id": account_id, "instrument_id": instrument_id, "marks": []}
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

    @api.get("/api/alerts/spec")
    async def alerts_spec(user: Me) -> dict[str, Any]:
        alert_service()
        return alert_spec()

    @api.get("/api/alerts")
    async def alerts_list(user: Me) -> list[dict[str, Any]]:
        return alert_service().list(user.id)

    @api.post("/api/alerts", status_code=201)
    async def alerts_create(body: AlertIn, user: Me) -> dict[str, Any]:
        try:
            return alert_service().create(user.id, body.model_dump())
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get("/api/alerts/webhook")
    async def alerts_webhook(user: Me) -> dict[str, Any]:
        return alert_service().webhook(user.id)

    @api.put("/api/alerts/webhook")
    async def alerts_webhook_set(body: WebhookIn, user: Me) -> dict[str, Any]:
        try:
            return alert_service().set_webhook(user.id, body.url)
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.delete("/api/alerts/webhook", status_code=204)
    async def alerts_webhook_clear(user: Me) -> Response:
        alert_service().clear_webhook(user.id)
        return Response(status_code=204)

    @api.post("/api/alerts/webhook/test")
    async def alerts_webhook_test(user: Me) -> dict[str, Any]:
        try:
            error = await alert_service().test_webhook(user.id)
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": not error, "error": error}

    # (after the webhook routes: /api/alerts/webhook is not an alert id)
    @api.patch("/api/alerts/{alert_id}")
    async def alerts_update(alert_id: str, body: AlertPatch, user: Me) -> dict[str, Any]:
        try:
            return alert_service().update(user.id, alert_id, body.model_dump(exclude_unset=True))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except AlertError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.delete("/api/alerts/{alert_id}", status_code=204)
    async def alerts_delete(alert_id: str, user: Me) -> Response:
        try:
            alert_service().delete(user.id, alert_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        return Response(status_code=204)

    # --- instruments and layouts -------------------------------------------------------------

    @api.get("/api/instruments")
    async def search_instruments(user: Me, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        live = platform.expiries
        found = platform.instruments.search(q, limit, keep=lambda i: not live.expired(i))
        return [
            instrument_to_dict(i, live) for i in found
        ]  # expired contracts don't come up (#290)

    @api.get("/api/instruments/{instrument_id}")
    async def get_instrument(instrument_id: str, user: Me) -> dict[str, Any]:
        instrument = platform.instruments.get(instrument_id)
        if instrument is None and (spread := book.visible(user, instrument_id)) is not None:
            tick = market(user).prices.latest(instrument_id)  # its implied price, if watched
            found = spread_instrument_to_dict(spread, platform.instruments.get)
            return {**found, "ltp_paise": tick.ltp_paise if tick else None}
        if instrument is None:
            raise HTTPException(404, "no such instrument")
        tick = market(user).prices.latest(instrument_id)  # the asker's own price (#395)
        found = instrument_to_dict(instrument, platform.expiries)
        return {**found, "ltp_paise": tick.ltp_paise if tick else None}

    @api.get("/api/instruments/{instrument_id}/vap")
    async def get_vap(instrument_id: str, user: Me) -> dict[str, Any]:
        """Today's volume at each price (an estimate from snapshots, see marketdata/vap.py)."""
        if platform.instruments.get(instrument_id) is None:
            raise HTTPException(404, "no such instrument")
        volume, levels = market(user).prices.vap.snapshot(instrument_id)
        return {"volume": volume, "levels": [[p, q] for p, q in sorted(levels.items())]}

    @api.get("/api/instruments/{instrument_id}/prints")
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

    @api.get("/api/market/movers")
    async def market_movers(user: Me, by: str = "gainers", limit: int = 10, exchange: str = "",
                            kind: str = "") -> dict[str, Any]:  # fmt: skip
        try:
            return market(user).movers.top(by, limit=limit, exchange=exchange, kind=kind)
        except MoversError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get("/api/accounts/{account_id}/instruments/{instrument_id}/order-spec")
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

    return api
