"""Risk on an account and the firm: MTM limits, square-off, lost stops, flatten, the kill
switches, surveillance, the auditor's export and trades made outside Laddrix.

    …    /api/accounts/{id}/mtm[/…]              MTM limits per position and portfolio
    …    /api/accounts/{id}/square-off           the square-off assistant
    …    /api/accounts/{id}/protection[/restore] what is unprotected; restore a stop (#264)
    …    /api/accounts/{id}/flatten              close positions on purpose (#262)
    POST /api/accounts/{id}/stop|resume          the account's kill switch
    POST /api/firm/stop|resume                   the firm's (resume needs step-up)
    GET  /api/surveillance; POST …/{id}/ack      patterns worth a look (#291)
    GET  /api/accounts/{id}/audit-export?day     one day's whole audit trail as CSV
    …    /api/accounts/{id}/external-trades      trades made outside Laddrix (#355)
"""

import csv
import io
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from laddrix.algos.engine import AlgoError
from laddrix.broker.base import BrokerError
from laddrix.charts.candles import IST
from laddrix.domain.accounts import Role, User, can_manage
from laddrix.domain.orders import Product
from laddrix.domain.portfolio import trade_to_dict
from laddrix.ops.protection import GUARANTEE, RestoreError
from laddrix.platform import AccountRuntime
from laddrix.positions.external import ExternalError
from laddrix.risk.kill_switch import stop_trading
from laddrix.risk.mtm import PortfolioRule, PositionRule
from laddrix.risk.surveillance import alert_to_dict as surveillance_to_dict
from laddrix.server.bodies import (
    AckIn,
    AdoptIn,
    ConfirmIn,
    FlattenIn,
    MtmRetryIn,
    PortfolioRuleIn,
    PositionRuleIn,
    RestoreIn,
    StopIn,
)
from laddrix.server.context import ServerContext, report_day, risk_or_admin
from laddrix.server.formats import algo_to_dict


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    users = ctx.users
    protection = ctx.protection
    require_step_up = ctx.require_step_up
    in_workspace = ctx.in_workspace
    runtime = ctx.runtime
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- MTM limits and the square-off assistant --------------------------------------------------

    def mtm_watch(running: AccountRuntime):
        if running.mtm is None:
            raise HTTPException(404, "no MTM watch for this account")
        return running.mtm

    @api.get("/api/accounts/{account_id}/mtm")
    async def mtm_rules(account_id: str, user: Me) -> dict[str, Any]:
        return mtm_watch(runtime(account_id, user)).to_dict()

    @api.put("/api/accounts/{account_id}/mtm/positions")
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

    @api.delete("/api/accounts/{account_id}/mtm/positions")
    async def mtm_position_off(account_id: str, instrument_id: str, product: Product,
                               user: Me) -> dict[str, Any]:  # fmt: skip
        watch = mtm_watch(runtime(account_id, user, trade=True))
        watch.remove_position_rule(f"{instrument_id}|{product.value}", user.id)
        return watch.to_dict()

    @api.post("/api/accounts/{account_id}/mtm/retry")
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

    @api.put("/api/accounts/{account_id}/mtm/portfolio")
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

    @api.delete("/api/accounts/{account_id}/mtm/portfolio")
    async def mtm_portfolio_off(account_id: str, user: Me) -> dict[str, Any]:
        watch = mtm_watch(runtime(account_id, user, trade=True))
        watch.set_portfolio_rule(None, user.id)
        return watch.to_dict()

    @api.get("/api/accounts/{account_id}/square-off")
    async def square_off_status(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.squareoff is None:
            raise HTTPException(404, "no square-off assistant for this account")
        return running.squareoff.status()

    @api.post("/api/accounts/{account_id}/square-off")
    async def square_off(account_id: str, body: ConfirmIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        if running.squareoff is None:
            raise HTTPException(404, "no square-off assistant for this account")
        if not body.confirm:
            raise HTTPException(422, "square-off needs the trader's confirmation")
        return {"closed": await running.squareoff.close_all(user.id)}

    # --- lost stops: what is unprotected, and restoring a stop (#264) ---------------------------

    @api.get("/api/accounts/{account_id}/protection")
    async def protection_status(account_id: str, user: Me) -> dict[str, Any]:
        runtime(account_id, user)
        rows = protection.status(account_id) if protection is not None else []
        return {"unprotected": rows, "guarantee": list(GUARANTEE)}

    @api.post("/api/accounts/{account_id}/protection/restore")
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
        if not ((account and can_manage(user, account)) or risk_or_admin(user)):
            raise HTTPException(403, "you can't close positions on this account")
        if running.flatten is None:
            raise HTTPException(404, "no flatten for this account")
        return running.flatten

    @api.get("/api/accounts/{account_id}/flatten")
    async def flatten_status(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        if running.flatten is None:
            raise HTTPException(404, "no flatten for this account")
        return running.flatten.status()

    @api.post("/api/accounts/{account_id}/flatten")
    async def flatten(account_id: str, body: FlattenIn, user: Me) -> dict[str, Any]:
        service = flattener(account_id, user)
        if not body.confirm:
            raise HTTPException(422, "flatten needs a confirmation")
        return await service.run(user.id, instrument_id=body.instrument_id,
                                 product=body.product)  # fmt: skip

    # --- surveillance and the auditor's export (#291) ---------------------------------------------

    def _risk_only(user: User) -> None:
        if not risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can see surveillance alerts")

    @api.get("/api/surveillance")
    async def surveillance_alerts(user: Me, open_only: bool = False) -> list[dict[str, Any]]:
        """Patterns worth a look (risk/surveillance.py): self-trades, large orders, cancel
        ratios, bursts. Kept in the audit trail; acknowledged with a note."""
        _risk_only(user)
        return [surveillance_to_dict(a) for a in platform.surveillance.alerts(open_only=open_only)
                if in_workspace(user, a.account_id)]  # fmt: skip

    @api.post("/api/surveillance/{alert_id}/ack")
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

    @api.get("/api/accounts/{account_id}/audit-export", response_model=None)
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
        name = f"laddrix-audit-{account_id}-{wanted.isoformat()}.csv"
        disposition = {"Content-Disposition": f'attachment; filename="{name}"'}
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers=disposition)  # fmt: skip

    # --- trades made outside Laddrix (#355) -----------------------------------------------------

    def _external(account_id: str, user: User):
        running = runtime(account_id, user)
        if not risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can resolve trades made outside "
                                "Laddrix")  # fmt: skip
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

    @api.get("/api/accounts/{account_id}/external-trades")
    async def preview_external(account_id: str, instrument_id: str, product: str,
                               user: Me) -> dict[str, Any]:  # fmt: skip
        """What adopting would do: the broker's trades made outside Laddrix in this position,
        and whether they explain the difference (positions/external.py). Changes nothing."""
        tool = _external(account_id, user)
        try:
            return _proposal(await tool.preview(instrument_id, _product(product)))
        except BrokerError as exc:
            raise HTTPException(502, f"the broker didn't answer: {exc.reason}") from None

    @api.post("/api/accounts/{account_id}/external-trades")
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

    @api.post("/api/accounts/{account_id}/stop")
    async def stop_account(account_id: str, body: StopIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        account = users.get_account(account_id)  # now (an admin may have changed traders)
        if not ((account and can_manage(user, account)) or risk_or_admin(user)):
            raise HTTPException(403, "you can't stop trading on this account")
        before = _stop_snapshot([running])
        failures = await stop_trading(running.switch, [running.orders], body.reason, by=user.id)
        reports = _stop_reports([running], before, failures, user.id, body.reason)
        return {"stopped": True, "failed_cancels": _failures(failures), "reports": reports}

    @api.post("/api/accounts/{account_id}/resume")
    async def resume_account(account_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user)
        owner = user.id == running.account.owner_user_id and user.has_role(Role.TRADER)
        if not (owner or risk_or_admin(user)):
            raise HTTPException(403, "only the account owner, risk or an admin can resume")
        running.switch.reset(by=user.id)
        return {"stopped": False}

    @api.post("/api/firm/stop")
    async def stop_firm(body: StopIn, user: Me) -> dict[str, Any]:
        if not risk_or_admin(user):
            raise HTTPException(403, "only risk or an admin can stop the whole firm")
        # the firm is the asker's workspace: its accounts and its switch, nobody else's (#368)
        runs = [r for r in platform.accounts.values() if r.account.workspace == user.workspace]
        before = _stop_snapshot(runs)
        managers = [r.orders for r in runs]
        firm = platform.firm_switch_of(user.workspace)
        failures = await stop_trading(firm, managers, body.reason, by=user.id)
        reports = _stop_reports(runs, before, failures, user.id, body.reason)
        return {"stopped": True, "failed_cancels": _failures(failures), "reports": reports}

    @api.post("/api/firm/resume")
    async def resume_firm(user: Me, request: Request) -> dict[str, Any]:
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can resume the whole firm")
        require_step_up(request, "resume the whole firm")
        platform.firm_switch_of(user.workspace).reset(by=user.id)
        return {"stopped": False}

    return api


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
