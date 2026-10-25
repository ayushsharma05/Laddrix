"""Baskets: several orders sent together, after one check of them all.

    check(legs) -> every leg through the risk checks (each seeing the legs before it as
                   working), priced as asked, plus the broker's margin for them together
                   (with the hedge benefit) where the broker can say
    send(legs)  -> the same check; nothing goes if any leg fails. Then option BUYS first,
                   and the rest only once every one of them has FILLED, so the account is
                   never short options without the protection it asked for. An accepted
                   buy is not yet protection: it may never fill, or be cancelled.
                   A buy refused, unconfirmed, cancelled or unfilled within the wait
                   (`fill_wait_s`) stops the basket: the unfilled rest of the buys is
                   cancelled and no short leg goes. The result says exactly what happened.

Each leg is an ordinary order (audited, cancelled and reconciled like any other) carrying
the basket id, so the order book can show the legs together.

Prices: a leg may give its own limit price, or ask for "touch" (buy at the offer, sell at
the bid: marketable) or "mid" (rounded to the tick on the side that doesn't overpay).
"""

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from laddrix.broker.base import BasketMargin, Broker, BrokerError, MarginQuoter
from laddrix.domain.instruments import Instrument, InstrumentKind
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from laddrix.domain.ticks import Tick
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import OrderError, OrderView, Pending
from laddrix.risk.checks import RiskGate

MAX_LEGS = 10
FILL_WAIT_S = 10.0  # how long a basket waits for its protective buys to fill
OPTIONS = (InstrumentKind.CALL, InstrumentKind.PUT)


class BasketError(ValueError):
    """A basket that can't be checked as asked; the message names the leg."""


@dataclass(frozen=True)
class LegIn:
    instrument_id: str
    side: Side
    lots: int
    product: Product = Product.NRML
    price_paise: int | None = None  # a limit price, or None to use `at`
    at: str = "touch"  # "touch" (marketable) or "mid"


@dataclass(frozen=True)
class LegCheck:
    request: OrderRequest
    problem: str | None


@dataclass(frozen=True)
class BasketCheck:
    legs: list[LegCheck]
    margin: BasketMargin | None
    margin_note: str  # why there's no margin figure, when there isn't

    @property
    def ok(self) -> bool:
        return all(leg.problem is None for leg in self.legs)


class Baskets:
    def __init__(
        self,
        account_id: str,
        orders: OrderManager,
        risk: RiskGate,
        broker: Broker,
        instruments: Callable[[str], Instrument | None],
        prices: Callable[[str], Tick | None],
        audit: Callable[[str, str, dict], None] | None = None,
        fill_wait_s: float = FILL_WAIT_S,
    ) -> None:
        self._fill_wait_s = fill_wait_s
        self._account_id = account_id
        self._orders = orders
        self._risk = risk
        self._broker = broker
        self._instrument = instruments
        self._price = prices
        self._audit = audit

    # --- building the orders ---

    def requests(self, legs: list[LegIn], user_id: str) -> list[OrderRequest]:
        if not 1 <= len(legs) <= MAX_LEGS:
            raise BasketError(f"a basket has 1 to {MAX_LEGS} legs")
        out = []
        for leg in legs:
            instrument = self._instrument(leg.instrument_id)
            if instrument is None:
                raise BasketError(f"unknown instrument {leg.instrument_id}")
            if not 1 <= leg.lots <= 1000:
                raise BasketError(f"{instrument.symbol}: lots must be 1 to 1000")
            price = leg.price_paise or self._limit(instrument, leg)
            out.append(
                OrderRequest(
                    client_order_id="",
                    account_id=self._account_id,
                    user_id=user_id,
                    instrument_id=instrument.id,
                    side=leg.side,
                    qty=leg.lots * instrument.lot_size,
                    order_type=OrderType.LIMIT,
                    product=leg.product,
                    price_paise=price,
                )
            )
        return out

    def _limit(self, instrument: Instrument, leg: LegIn) -> int:
        tick = self._price(instrument.id)
        bid = tick.best_bid if tick else None
        ask = tick.best_ask if tick else None
        buying = leg.side is Side.BUY
        if leg.at == "mid":
            if bid is None or ask is None:
                raise BasketError(f"{instrument.symbol}: no two-sided market for a mid price")
            mid = (bid.price_paise + ask.price_paise) / 2
            step = instrument.tick_paise
            snapped = (mid // step) * step if buying else -((-mid) // step) * step
            return int(snapped)
        touch = ask if buying else bid
        if touch is None:
            raise BasketError(f"{instrument.symbol}: no {'offer' if buying else 'bid'} to trade at")
        return touch.price_paise

    # --- check and send ---

    async def check(self, legs: list[LegIn], user_id: str) -> BasketCheck:
        requests = self.requests(legs, user_id)
        ordered = self._send_order(requests)  # checked in the order they'd be sent
        problems = await self._risk.check_basket(ordered)
        by_request = {id(r): p for r, p in zip(ordered, problems, strict=True)}
        checks = [LegCheck(r, by_request[id(r)]) for r in requests]
        margin, note = None, ""
        if isinstance(self._broker, MarginQuoter):
            try:
                margin = await self._broker.basket_margin(requests)
            except BrokerError as exc:
                note = exc.reason
        else:
            note = "this broker doesn't quote margins (paper)"
        return BasketCheck(checks, margin, note)

    async def send(self, legs: list[LegIn], user_id: str) -> dict[str, Any]:
        checked = await self.check(legs, user_id)
        if not checked.ok:
            return {"basket_id": None, "sent": [], "not_sent": _legs(checked), "check": checked}
        basket_id = f"B{time.strftime('%y%m%d')}{secrets.token_hex(3).upper()}"
        ordered = self._send_order([c.request for c in checked.legs])
        buys = [r for r in ordered if self._protects(r)]
        sent: list[OrderView] = []
        stopped = await self._submit(buys, basket_id, sent)
        rest = ordered[len(buys) :]
        if not stopped and buys and rest:
            stopped = await self._await_protection(sent)
        if not stopped:
            stopped = await self._submit(rest, basket_id, sent)
        sent = [self._orders.get(v.client_order_id) or v for v in sent]  # latest states
        not_sent = [_leg_dict(r) for r in ordered[len(sent) :]]
        if stopped and not_sent:
            stopped = f"{stopped}; {len(not_sent)} legs not sent"
        if self._audit is not None:
            self._audit(
                "BASKET_STOPPED" if stopped else "BASKET_SENT",
                stopped or f"basket {basket_id}: {len(sent)} legs sent",
                {
                    "basket_id": basket_id,
                    "orders": [v.client_order_id for v in sent],
                    "user_id": user_id,
                },
            )
        return {"basket_id": basket_id, "sent": sent, "not_sent": not_sent, "stopped": stopped}

    async def _submit(
        self, requests: list[OrderRequest], basket_id: str, sent: list[OrderView]
    ) -> str:
        """Send legs one by one; the first refused or unconfirmed one stops the rest."""
        for request in requests:
            view = await self._orders.submit(replace(request, basket_id=basket_id))
            sent.append(view)
            unknown = view.pending is Pending.UNCONFIRMED  # no clear answer: don't pile on
            if view.status is OrderStatus.REJECTED or unknown:
                what = "unconfirmed" if unknown else "refused"
                return f"{request.instrument_id} {what}: {view.reason}"
        return ""

    async def _await_protection(self, buys: list[OrderView]) -> str:
        """Wait, on order events (no polling), until every protective buy has filled.
        Returns "" when all have, else why not, after cancelling what is still unfilled."""
        ids = [v.client_order_id for v in buys]
        changed = asyncio.Event()
        stop_listening = self._orders.subscribe(
            lambda e: changed.set() if e.order and e.order.client_order_id in ids else None
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._fill_wait_s
        try:
            while True:
                views = [self._orders.get(i) for i in ids]
                if all(v is not None and v.status is OrderStatus.FILLED for v in views):
                    return ""
                failed = next(
                    (v for v in views if v is not None and v.status.is_final
                     and v.status is not OrderStatus.FILLED),
                    None,
                )  # fmt: skip
                if failed is not None:
                    why = f"{failed.request.instrument_id} {failed.status.value.lower()}"
                    break
                left = deadline - loop.time()
                if left <= 0:
                    why = f"protective buys not filled within {self._fill_wait_s:g} s"
                    break
                changed.clear()
                try:
                    await asyncio.wait_for(changed.wait(), left)
                except TimeoutError:
                    pass
        finally:
            stop_listening()
        for i in ids:  # an unfilled rest protects nothing on its own: pull it
            view = self._orders.get(i)
            if view is not None and not view.status.is_final:
                with contextlib.suppress(OrderError):
                    await self._orders.cancel(i)
        return f"{why}: short legs not sent, unfilled protective buys cancelled"

    def _protects(self, r: OrderRequest) -> bool:
        instrument = self._instrument(r.instrument_id)
        return r.side is Side.BUY and instrument is not None and instrument.kind in OPTIONS

    def _send_order(self, requests: list[OrderRequest]) -> list[OrderRequest]:
        """Option buys first (they protect the sells), then everything else, as given."""
        return [r for r in requests if self._protects(r)] + [
            r for r in requests if not self._protects(r)
        ]


def _leg_dict(r: OrderRequest) -> dict[str, Any]:
    return {
        "instrument_id": r.instrument_id,
        "side": r.side.value,
        "qty": r.qty,
        "price_paise": r.price_paise,
    }


def _legs(checked: BasketCheck) -> list[dict[str, Any]]:
    return [{**_leg_dict(c.request), "problem": c.problem} for c in checked.legs]


def check_to_dict(checked: BasketCheck) -> dict[str, Any]:
    return {
        "ok": checked.ok,
        "legs": _legs(checked),
        "margin": (
            {
                "initial_paise": checked.margin.initial_paise,
                "final_paise": checked.margin.final_paise,
                "hedge_benefit_paise": checked.margin.initial_paise - checked.margin.final_paise,
            }
            if checked.margin
            else None
        ),
        "margin_note": checked.margin_note,
    }
