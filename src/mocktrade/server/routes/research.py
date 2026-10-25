"""Research (K11): recorded days of ticks, backtests over them, imported candles.

GET    /api/research/recordings[/{day}]   the recorded days; DELETE one (admin)
POST   /api/research/backtests            start one; GET lists, …/{id} reads, …/cancel stops
GET    /api/research/backtests/form       what a backtest can be asked (the form's spec)
GET    /api/research/backtests/{id}/days/{day}  one day's report (json, csv, pdf)
POST   /api/research/candles              import a day's candles (admin)
"""

import asyncio
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response

from mocktrade.domain.accounts import Role, User
from mocktrade.reports.export import report_csv, report_pdf
from mocktrade.research.backtest import (
    BacktestBusy,
    BacktestError,
    BacktestSpec,
    CandleStore,
    form_spec,
)
from mocktrade.research.ticks import TickStore
from mocktrade.server.bodies import BacktestIn, CandlesIn
from mocktrade.server.context import ServerContext


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    replay_command = ctx.replay_command
    backtests = ctx.backtests
    candles = ctx.candles
    candles_for = ctx.candles_for
    market = ctx.market
    admin_of = ctx.admin_of
    platform_wide = ctx.platform_wide
    audit = ctx.audit
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- research: recorded ticks (K11) ---------------------------------------------------------

    def recordings(user: User) -> TickStore:
        """The recorded days of the asker's own subscription (#396); on a simulator or a
        replay, MockTrade's own, everyone's."""
        found = market(user).ticks
        if found is None:
            raise HTTPException(404, "ticks aren't being recorded on this server")
        return found

    @api.get("/api/research/recordings")
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

    @api.get("/api/research/recordings/{day}")
    async def recorded_day(day: str, user: Me) -> dict[str, Any]:
        store = recordings(user)
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, f"not a date: {day!r} (YYYY-MM-DD)") from None
        if when not in store.days():
            raise HTTPException(404, f"nothing recorded on {day}")
        return await asyncio.to_thread(store.summary, when)

    @api.delete("/api/research/recordings/{day}")
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

    @api.post("/api/research/backtests", status_code=201)
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

    @api.get("/api/research/backtests")
    async def backtest_list(user: Me) -> list[dict[str, Any]]:
        admin = user.has_role(Role.ADMIN)
        return [r.to_dict(full=False) for r in backtest_runs().runs()
                if r.may_see(user.id, admin and admin_of(user, r.owner))]  # fmt: skip

    @api.get("/api/research/backtests/form")
    async def backtest_form_spec(user: Me, instrument_id: str = "") -> dict[str, Any]:
        backtest_runs()
        store = market(user).ticks  # their own subscription's days (#396)
        days = await asyncio.to_thread(store.days) if store is not None else []
        own = candle_store(user)
        out = form_spec(days, candles=own is not None)
        if own is not None and instrument_id:
            out["candle_days"] = await asyncio.to_thread(own.days, instrument_id)
        return out

    @api.get("/api/research/backtests/{run_id}/days/{day}", response_model=None)
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

    @api.get("/api/research/backtests/{run_id}")
    async def backtest_get(run_id: str, user: Me) -> dict[str, Any]:
        return own_run(run_id, user).to_dict()

    @api.post("/api/research/backtests/{run_id}/cancel")
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

    @api.post("/api/research/candles", status_code=201)
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

    return api
