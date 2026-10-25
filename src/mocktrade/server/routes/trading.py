"""Trading one account: orders, baskets, GTTs, algos, charges and margin.

GET/POST   /api/accounts/{id}/orders                  the order book; place an order
PATCH/DELETE /api/accounts/{id}/orders/{cid}          modify; cancel
POST       /api/accounts/{id}/orders/{cid}/settle     an unanswered order checked at the broker
GET        /api/accounts/{id}/orders/{cid}/history    one order's events
GET        /api/accounts/{id}/events                  the account's audit trail
POST       /api/accounts/{id}/baskets/check|send      several orders at once
…          /api/accounts/{id}/gtt[/{gtt_id}]          orders the broker holds until a trigger
…          /api/accounts/{id}/algos[/{algo_id}/…]     start, act, pause, resume, stop
GET        /api/charges/schedule; /api/accounts/{id}/charges[/estimate]; …/margin
"""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from mocktrade.algos.engine import AlgoError, algo_id_note
from mocktrade.algos.spec import spec_to_dict as algo_spec_to_dict
from mocktrade.charges.model import bill_to_dict
from mocktrade.charges.schedules import schedule_to_dict
from mocktrade.charges.service import ChargesError
from mocktrade.domain.accounts import User
from mocktrade.domain.gtt import gtt_spec_to_dict, gtt_to_dict
from mocktrade.domain.orders import OrderRequest, OrderType
from mocktrade.orders.basket import BasketError, LegIn, check_to_dict
from mocktrade.orders.codec import view_to_dict
from mocktrade.orders.gtt import GttError, gtt_request_from
from mocktrade.orders.model import OrderError
from mocktrade.platform import AccountRuntime
from mocktrade.server.bodies import (
    ActionIn,
    AlgoIn,
    BasketIn,
    ChargesIn,
    ConfirmIn,
    GttIn,
    ModifyIn,
    OrderIn,
)
from mocktrade.server.context import ServerContext
from mocktrade.server.formats import (
    algo_to_dict,
    event_to_dict,
)


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    runtime = ctx.runtime
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- baskets -------------------------------------------------------------------------------

    def basket_legs(body: BasketIn) -> list[LegIn]:
        return [LegIn(**leg.model_dump()) for leg in body.legs]

    @api.post("/api/accounts/{account_id}/baskets/check")
    async def basket_check(account_id: str, body: BasketIn, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            return check_to_dict(await running.baskets.check(basket_legs(body), user.id))
        except BasketError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.post("/api/accounts/{account_id}/baskets/send")
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

    @api.get("/api/charges/schedule")
    async def charge_schedule(user: Me) -> dict[str, Any]:
        return schedule_to_dict(platform.charges_model.schedule)

    @api.get("/api/accounts/{account_id}/charges")
    async def charges_today(account_id: str, user: Me) -> dict[str, Any]:
        service = charges_service(runtime(account_id, user))
        return {"account_id": account_id,
                **bill_to_dict(service.bill(), service.model.schedule)}  # fmt: skip

    @api.post("/api/accounts/{account_id}/charges/estimate")
    async def charges_estimate(account_id: str, body: ChargesIn, user: Me) -> dict[str, Any]:
        service = charges_service(runtime(account_id, user))
        try:
            bill = service.estimate(body.instrument_id, body.side, body.qty,
                                    body.price_paise, body.product)  # fmt: skip
        except ChargesError as exc:
            raise HTTPException(422, str(exc)) from None
        return bill_to_dict(bill, service.model.schedule)

    # --- margin: what an order would block, against what's available -------------------------

    @api.post("/api/accounts/{account_id}/margin")
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

    # --- GTT (held by the broker) ------------------------------------------------------------

    def gtt_service(running: AccountRuntime):
        if running.gtt is None:
            raise HTTPException(404, f"{running.broker.name} doesn't hold GTT orders")
        return running.gtt

    def gtt_request(body: GttIn, account_id: str, user: User):
        return gtt_request_from(body.model_dump(mode="json"), account_id, user.id)

    @api.get("/api/accounts/{account_id}/gtt")
    async def gtt_list(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [gtt_to_dict(v) for v in gtt_service(runtime(account_id, user)).list()]

    @api.get("/api/accounts/{account_id}/gtt-spec")
    async def gtt_spec(account_id: str, instrument_id: str, user: Me) -> dict[str, Any]:
        """What the broker allows for GTTs on this instrument: the screens draw from it."""
        try:
            return gtt_spec_to_dict(gtt_service(runtime(account_id, user)).spec(instrument_id))
        except GttError as exc:
            raise HTTPException(404, str(exc)) from None

    @api.post("/api/accounts/{account_id}/gtt", status_code=201)
    async def gtt_place(account_id: str, body: GttIn, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.place(gtt_request(body, account_id, user)))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.put("/api/accounts/{account_id}/gtt/{gtt_id}")
    async def gtt_modify(account_id: str, gtt_id: str, body: GttIn, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.modify(gtt_id, gtt_request(body, account_id, user)))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.delete("/api/accounts/{account_id}/gtt/{gtt_id}")
    async def gtt_delete(account_id: str, gtt_id: str, user: Me) -> dict[str, Any]:
        service = gtt_service(runtime(account_id, user, trade=True))
        try:
            return gtt_to_dict(await service.delete(gtt_id, user.id))
        except GttError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- orders ------------------------------------------------------------------------------

    @api.get("/api/accounts/{account_id}/orders")
    async def list_orders(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [view_to_dict(v) for v in runtime(account_id, user).orders.orders()]

    @api.post("/api/accounts/{account_id}/orders", status_code=201)
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
        try:
            return view_to_dict(await running.orders.submit(request))
        except OrderError as exc:  # e.g. an order id reused for a different order (#279)
            raise HTTPException(409, exc.reason) from None

    @api.patch("/api/accounts/{account_id}/orders/{client_order_id}")
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

    @api.delete("/api/accounts/{account_id}/orders/{client_order_id}")
    async def cancel(account_id: str, client_order_id: str, user: Me) -> dict[str, Any]:
        running = runtime(account_id, user, trade=True)
        try:
            return view_to_dict(await running.orders.cancel(client_order_id))
        except OrderError as exc:
            raise HTTPException(409, exc.reason) from None

    @api.post("/api/accounts/{account_id}/orders/{client_order_id}/settle")
    async def settle(account_id: str, client_order_id: str, body: ConfirmIn,
                     user: Me) -> dict[str, Any]:  # fmt: skip
        """An unanswered order a person checked at the broker: not there, so never placed
        (#259). Only after confirming; recorded with who did it."""
        running = runtime(account_id, user, trade=True)
        if not body.confirm:
            raise HTTPException(422, "check the broker's own order book first, then confirm")
        try:
            return view_to_dict(running.orders.settle_unconfirmed(client_order_id, by=user.id))
        except OrderError as exc:
            raise HTTPException(409, exc.reason) from None

    @api.get("/api/accounts/{account_id}/orders/{client_order_id}/history")
    async def history(account_id: str, client_order_id: str, user: Me) -> list[dict[str, Any]]:
        running = runtime(account_id, user)
        if platform.event_log is None or running.orders.get(client_order_id) is None:
            raise HTTPException(404, "no such order")
        events = await asyncio.to_thread(platform.event_log.history, client_order_id)
        return [event_to_dict(e) for e in events if e.account_id == account_id]

    @api.get("/api/accounts/{account_id}/events")
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
        names = [account_id, f"account {account_id}", platform.firm_switch_of(user.workspace).name]
        events = await asyncio.to_thread(
            platform.event_log.page,
            names,
            before_seq=before_seq,
            after_seq=after_seq,
            limit=limit,
        )
        return [event_to_dict(e) for e in events]

    # --- algos -----------------------------------------------------------------------------
    # Specs describe each algo's settings; the screen draws its form from them (algos/spec.py).

    @api.get("/api/accounts/{account_id}/algo-specs")
    async def algo_specs(account_id: str, user: Me) -> list[dict[str, Any]]:
        engine = runtime(account_id, user).algos
        return [
            {**algo_spec_to_dict(t.spec), "exchange_algo_id": t.exchange_algo_id,
             "exchange_algo_note": algo_id_note(t)}  # said, not hidden (#291)
            for t in engine.types.values()
        ]  # fmt: skip

    @api.get("/api/accounts/{account_id}/algos")
    async def list_algos(account_id: str, user: Me) -> list[dict[str, Any]]:
        return [algo_to_dict(a) for a in runtime(account_id, user).algos.algos()]

    @api.post("/api/accounts/{account_id}/algos", status_code=201)
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

    @api.get("/api/accounts/{account_id}/algos/{algo_id}/actions/{name}")
    async def preview_algo_action(
        account_id: str, algo_id: str, name: str, user: Me
    ) -> dict[str, Any]:
        """What one of the algo's own actions would do now, in words (#425)."""
        running = runtime(account_id, user, trade=True)
        try:
            p = running.algos.preview(algo_id, name)
        except AlgoError as exc:
            raise HTTPException(_action_status(exc), exc.reason) from None
        return {"name": p.name, "label": p.label, "lines": list(p.lines), "ok": p.ok,
                "why": p.why}  # fmt: skip

    @api.post("/api/accounts/{account_id}/algos/{algo_id}/actions/{name}")
    async def do_algo_action(
        account_id: str, algo_id: str, name: str, body: ActionIn, user: Me
    ) -> dict[str, Any]:
        """Do it, only as previewed: if it would do something else now, 409 says what."""
        running = runtime(account_id, user, trade=True)
        try:
            view = running.algos.act(algo_id, name, body.expect)
        except AlgoError as exc:
            raise HTTPException(_action_status(exc), exc.reason) from None
        return algo_to_dict(view)

    @api.post("/api/accounts/{account_id}/algos/{algo_id}/{action}")
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

    return api


def _action_status(exc: AlgoError) -> int:
    """No such algo or action: 404; it can't be done now, or has changed: 409."""
    missing = exc.reason.startswith("no algo ") or "has no action" in exc.reason
    return 404 if missing else 409
