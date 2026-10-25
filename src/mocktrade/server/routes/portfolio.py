"""What an account holds and made: positions, end-of-day reports, P&L history, and the
broker's funds, holdings and tradebook (read-only).

    GET /api/accounts/{id}/positions           positions, P&L, recovery and reconciliation
    GET /api/accounts/{id}/reports[/{day}]     the kept days; one day's report (json, csv, pdf)
    GET /api/accounts/{id}/pnl                 P&L by day, week or month
    GET /api/accounts/{id}/funds|holdings|trades  from the broker
"""

import asyncio
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response

from mocktrade.domain.accounts import User
from mocktrade.portfolio.service import PortfolioError, snapshot_to_dict
from mocktrade.reports.export import report_csv, report_pdf
from mocktrade.reports.service import ReportError, Reports
from mocktrade.server.context import ServerContext, report_day
from mocktrade.server.formats import (
    position_to_dict,
)


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    reports = ctx.reports
    runtime = ctx.runtime
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- end-of-day reports and P&L history ---------------------------------------------------

    day_reports = reports or Reports(platform, None)

    @api.get("/api/accounts/{account_id}/reports")
    async def report_days(account_id: str, user: Me) -> dict[str, Any]:
        runtime(account_id, user)
        return {"today": day_reports.today().isoformat(),
                "kept": [d.isoformat() for d in day_reports.kept_days(account_id)]}  # fmt: skip

    @api.get("/api/accounts/{account_id}/reports/{day}", response_model=None)
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

    @api.get("/api/accounts/{account_id}/pnl")
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

    @api.get("/api/accounts/{account_id}/funds")
    async def funds(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "funds", fresh, user)

    @api.get("/api/accounts/{account_id}/holdings")
    async def holdings(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "holdings", fresh, user)

    @api.get("/api/accounts/{account_id}/trades")
    async def trades(account_id: str, user: Me, fresh: bool = False) -> dict[str, Any]:
        return await portfolio(account_id, "trades", fresh, user)

    # --- positions -----------------------------------------------------------------------------

    @api.get("/api/accounts/{account_id}/positions")
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

    return api
