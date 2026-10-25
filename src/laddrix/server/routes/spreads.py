"""Spreads (#414) and structure positions (#428): each user's multi-leg definitions, trading
them with the Autospreader, and grouping an account's fills into structures.

    GET            /api/spreads/spec                       what a spread's controls are
    GET/POST       /api/spreads                            my spreads; define one
    PUT/DELETE     /api/spreads/{id}                       change one; delete one
    POST           /api/accounts/{id}/spreads/{sid}/orders buy or sell spread lots
    GET/POST       /api/accounts/{id}/structures           structures; make one from a spread
    DELETE/POST    /api/accounts/{id}/structures/{s}/…     delete; allocate, remove, move fills
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from laddrix.algos.autospreader import SPEC as ASE
from laddrix.algos.engine import AlgoError
from laddrix.algos.spec import spec_to_dict as algo_spec_to_dict
from laddrix.domain.accounts import User
from laddrix.domain.orders import Side
from laddrix.server.bodies import AllocationIn, MoveIn, SpreadIn, SpreadOrderIn, StructureIn
from laddrix.server.context import ServerContext
from laddrix.server.formats import algo_to_dict
from laddrix.spreads.model import (
    CONTROLS,
    PREFIX,
    Controls,
    SpreadDef,
    SpreadLeg,
    controls_from_dict,
    spread_tick,
    spread_to_dict,
)
from laddrix.spreads.store import SpreadError
from laddrix.structures.book import StructureBook, StructureError, StructureLeg


def router(ctx: ServerContext) -> APIRouter:
    api = APIRouter()
    platform = ctx.platform
    book = ctx.book
    runtime = ctx.runtime
    Me = Annotated[User, Depends(ctx.current_user)]

    # --- spreads (#414): each user's own multi-leg definitions --------------------------------

    def _legs(body: SpreadIn) -> list[SpreadLeg]:
        return [SpreadLeg(x.instrument_id, x.ratio, x.multiplier, x.quote, x.payup_ticks)
                for x in body.legs]  # fmt: skip

    def _controls(body: SpreadIn) -> Controls | None:
        if body.controls is None:
            return None
        controls, problems = controls_from_dict(body.controls)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        return controls

    def _spread_out(s: SpreadDef) -> dict[str, Any]:
        return {**spread_to_dict(s), "tick_paise": spread_tick(s.legs, platform.instruments.get)}

    @api.get("/api/spreads/spec")
    async def spread_spec(user: Me) -> dict[str, Any]:
        """What a spread's controls are (labels, limits, defaults, help): the screens draw
        their fields from this (#425)."""
        return {"controls": algo_spec_to_dict(CONTROLS)["params"]}

    @api.get("/api/spreads")
    async def list_spreads(user: Me) -> list[dict[str, Any]]:
        return [_spread_out(s) for s in book.mine(user)]

    @api.post("/api/spreads", status_code=201)
    async def create_spread(body: SpreadIn, user: Me) -> dict[str, Any]:
        try:
            made = book.create(user, body.name, _legs(body), _controls(body))
        except SpreadError as exc:
            raise HTTPException(422, str(exc)) from None
        await book.save(user.id)
        return _spread_out(made)

    @api.put("/api/spreads/{spread_id}")
    async def update_spread(spread_id: str, body: SpreadIn, user: Me) -> dict[str, Any]:
        try:
            changed = book.update(user, spread_id, body.name, _legs(body), _controls(body))
        except SpreadError as exc:
            raise HTTPException(404 if exc.missing else 422, str(exc)) from None
        await book.save(user.id)
        return _spread_out(changed)

    @api.post("/api/accounts/{account_id}/spreads/{spread_id}/orders", status_code=201)
    async def trade_spread(
        account_id: str, spread_id: str, body: SpreadOrderIn, user: Me
    ) -> dict[str, Any]:
        """Buy or sell spread lots at a spread price: the ASE algo (#415) quotes the quoting
        leg and hedges the others. The server works out the legs; the screens never do."""
        running = runtime(account_id, user, trade=True)
        spread = book.visible(user, PREFIX + spread_id)
        if spread is None:
            raise HTTPException(404, f"no spread {spread_id}")
        q = spread.quote_leg
        instrument = platform.instruments.get(q.instrument_id)
        if instrument is None:
            raise HTTPException(422, f"{q.instrument_id} is not a known contract")
        buys_q = (q.ratio > 0) == (body.side is Side.BUY)
        try:
            view = running.algos.start(
                ASE.code,
                user_id=user.id,
                instrument_id=q.instrument_id,
                side=Side.BUY if buys_q else Side.SELL,
                qty=body.lots * abs(q.ratio) * instrument.lot_size,
                params={"spread": spread.spread_id, "level_paise": body.price_paise},
                product=body.product,
            )
        except AlgoError as exc:
            raise HTTPException(422, exc.reason) from None
        return algo_to_dict(view)

    @api.delete("/api/spreads/{spread_id}", status_code=204)
    async def delete_spread(spread_id: str, user: Me) -> None:
        try:
            book.delete(user, spread_id)
        except SpreadError as exc:
            raise HTTPException(404, str(exc)) from None
        await book.save(user.id)

    # --- structure positions (#428) ------------------------------------------------------------

    def structures_of(account_id: str, user: User, *, trade: bool = False) -> StructureBook:
        running = runtime(account_id, user, trade=trade)
        if running.structures is None:
            raise HTTPException(404, "this account has no structures")
        return running.structures

    def structure_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except StructureError as exc:
            raise HTTPException(404 if exc.missing else 422, exc.reason) from None

    @api.get("/api/accounts/{account_id}/structures")
    async def list_structures(account_id: str, user: Me) -> dict[str, Any]:
        """Each structure's spread lots, legs and P&L; what is unallocated per instrument; and
        the fills not yet in a structure. Worked out on the server; the screens show it."""
        book_ = structures_of(account_id, user)
        return {"structures": book_.structures(), "unallocated": book_.unallocated(),
                "free_fills": book_.free_fills()}  # fmt: skip

    @api.post("/api/accounts/{account_id}/structures", status_code=201)
    async def create_structure(account_id: str, body: StructureIn, user: Me) -> dict[str, Any]:
        book_ = structures_of(account_id, user, trade=True)
        spread = book.visible(user, PREFIX + body.spread_id)
        if spread is None:
            raise HTTPException(404, f"no spread {body.spread_id}")
        legs = [StructureLeg(g.instrument_id, g.ratio, g.multiplier) for g in spread.legs]
        made = structure_call(lambda: book_.create(body.name or spread.name, legs, user.id,
                                                   spread.spread_id))  # fmt: skip
        return book_.get(made.structure_id)

    @api.delete("/api/accounts/{account_id}/structures/{structure_id}", status_code=204)
    async def delete_structure(account_id: str, structure_id: str, user: Me) -> None:
        book_ = structures_of(account_id, user, trade=True)
        structure_call(lambda: book_.delete(structure_id, user.id))

    @api.post("/api/accounts/{account_id}/structures/{structure_id}/allocations")
    async def allocate_fill(
        account_id: str, structure_id: str, body: AllocationIn, user: Me
    ) -> dict[str, Any]:
        book_ = structures_of(account_id, user, trade=True)
        structure_call(lambda: book_.allocate(structure_id, body.client_order_id, body.qty,
                                              user.id))  # fmt: skip
        return book_.get(structure_id)

    @api.post("/api/accounts/{account_id}/structures/{structure_id}/allocations/remove")
    async def remove_fill(
        account_id: str, structure_id: str, body: AllocationIn, user: Me
    ) -> dict[str, Any]:
        book_ = structures_of(account_id, user, trade=True)
        structure_call(lambda: book_.remove(structure_id, body.client_order_id, body.qty,
                                            user.id))  # fmt: skip
        return book_.get(structure_id)

    @api.post("/api/accounts/{account_id}/structures/{structure_id}/move")
    async def move_fill(
        account_id: str, structure_id: str, body: MoveIn, user: Me
    ) -> dict[str, Any]:
        book_ = structures_of(account_id, user, trade=True)
        structure_call(lambda: book_.move(structure_id, body.to, body.client_order_id,
                                          body.qty, user.id))  # fmt: skip
        return {"from": book_.get(structure_id), "to": book_.get(body.to)}

    return api
