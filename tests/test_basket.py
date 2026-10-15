"""Baskets: one check for all legs, protective buys first, stop at a refusal, margins."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from mocktrade.broker.base import BasketMargin
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.kite.broker import KiteBroker
from mocktrade.broker.kite.http import KiteHttp
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
)
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.basket import BasketError, LegIn
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.store.events import EventLog

K = InstrumentKind
EXP = date(2026, 10, 27)


def opt(strike, kind, expiry=EXP):
    return Instrument(
        Exchange.NFO, f"NIFTY26OCT{strike}{kind.value}", kind, 5, 75, expiry, strike * 100, "NIFTY"
    )


class QuotingBroker(FakeBroker):
    """A fake broker that also quotes basket margins (as Kite does)."""

    async def basket_margin(self, requests):
        return BasketMargin(initial_paise=len(requests) * 10_000_00, final_paise=3_000_00)


class FillingBroker(FakeBroker):
    """A fake broker whose market fills buys at their limit a moment after they're placed
    (or only part of them, with `part`); sells are left working."""

    def __init__(self, part=None):
        super().__init__()
        self.manager = None
        self.part = part
        self.fill_buys = True

    async def place(self, request):
        broker_id = await super().place(request)
        if request.side is Side.BUY and self.fill_buys:
            qty = self.part or request.qty
            status = OrderStatus.FILLED if qty >= request.qty else OrderStatus.PARTIAL
            update = OrderUpdate(
                request.client_order_id, broker_id, status, qty, request.price_paise,
                last_fill_qty=qty, last_fill_price_paise=request.price_paise,
            )  # fmt: skip
            asyncio.get_running_loop().call_soon(self.manager.apply, update)
        return broker_id


def world(tmp_path, broker=None, **limits):
    store, md = InstrumentStore(), MarketData()
    for k in (24800, 24900, 25000, 25100, 25200):
        for kind in (K.CALL, K.PUT):
            store.add(opt(k, kind))
            price = 10000 + (25000 - k) * (1 if kind is K.CALL else -1) // 2
            md.on_tick(
                Tick(
                    opt(k, kind).id,
                    price,
                    bids=(DepthLevel(price - 10, 750, 3),),
                    asks=(DepthLevel(price + 10, 750, 3),),
                )
            )
    log = EventLog(tmp_path / "events.db")
    log.start()
    platform = Platform(store, md, KillSwitch("firm"), log)
    broker = broker or FakeBroker()
    runtime = platform.add_account(
        BrokerAccount("acc", "paper", "P1", "ayush"),
        broker,
        ids=ClientIds("MT"),
        limits=RiskLimits(**limits),
    )
    if isinstance(broker, FillingBroker):
        broker.manager = runtime.orders
    runtime.baskets._fill_wait_s = 0.3
    return runtime, broker, log


def hold(runtime, positions):
    """Pretend the account holds these net positions (both views the risk gate reads)."""
    runtime.risk._positions = lambda: dict(positions)
    runtime.risk._position = lambda i: positions.get(i, 0)


def condor():
    return [
        LegIn(opt(24900, K.PUT).id, Side.SELL, 1),
        LegIn(opt(24800, K.PUT).id, Side.BUY, 1),
        LegIn(opt(25100, K.CALL).id, Side.SELL, 1),
        LegIn(opt(25200, K.CALL).id, Side.BUY, 1),
    ]


async def test_legs_are_priced_at_the_touch_or_the_mid_on_the_tick_grid(tmp_path):
    runtime, *_ = world(tmp_path)
    b = runtime.baskets
    buy_touch, sell_touch, buy_mid, sell_mid, own = b.requests(
        [
            LegIn(opt(25000, K.CALL).id, Side.BUY, 1),
            LegIn(opt(25000, K.CALL).id, Side.SELL, 1),
            LegIn(opt(25000, K.CALL).id, Side.BUY, 1, at="mid"),
            LegIn(opt(25000, K.CALL).id, Side.SELL, 1, at="mid"),
            LegIn(opt(25000, K.CALL).id, Side.BUY, 2, price_paise=9500),
        ],
        "ayush",
    )
    assert (buy_touch.price_paise, sell_touch.price_paise) == (10010, 9990)  # marketable
    assert (buy_mid.price_paise, sell_mid.price_paise) == (10000, 10000)
    assert (own.price_paise, own.qty, own.order_type) == (9500, 150, OrderType.LIMIT)
    with pytest.raises(BasketError, match="unknown instrument"):
        b.requests([LegIn("NFO:NOPE", Side.BUY, 1)], "ayush")
    with pytest.raises(BasketError, match="1 to 10 legs"):
        b.requests([], "ayush")


async def test_an_iron_condor_is_covered_so_its_short_legs_pass(tmp_path):
    runtime, broker, log = world(tmp_path)  # option selling NOT enabled
    checked = await runtime.baskets.check(condor(), "ayush")
    assert checked.ok, [c.problem for c in checked.legs]
    assert checked.margin is None and "paper" in checked.margin_note
    await log.aclose()


async def test_a_naked_short_is_refused_and_nothing_is_sent(tmp_path):
    runtime, broker, log = world(tmp_path)
    result = await runtime.baskets.send(
        [LegIn(opt(25100, K.CALL).id, Side.SELL, 1), LegIn(opt(24800, K.PUT).id, Side.BUY, 1)],
        "ayush",
    )
    assert result["basket_id"] is None and broker.placed == []
    problems = {leg["instrument_id"]: leg["problem"] for leg in result["not_sent"]}
    assert "not enabled" in problems[opt(25100, K.CALL).id]
    assert problems[opt(24800, K.PUT).id] is None  # the put buy alone was fine
    await log.aclose()


async def test_protective_buys_go_first_and_every_leg_carries_the_basket_id(tmp_path):
    runtime, broker, log = world(tmp_path, broker=FillingBroker())
    result = await runtime.baskets.send(condor(), "ayush")
    assert result["stopped"] == "" and result["not_sent"] == []
    assert [(r.side, r.instrument_id[-7:]) for r in broker.placed] == [
        (Side.BUY, "24800PE"),
        (Side.BUY, "25200CE"),
        (Side.SELL, "24900PE"),
        (Side.SELL, "25100CE"),
    ]
    basket = result["basket_id"]
    assert basket.startswith("B") and {r.basket_id for r in broker.placed} == {basket}
    assert all(v.request.basket_id == basket for v in runtime.orders.orders())
    await log.flush()
    notes = [e for e in log.events(account_id="acc") if e.kind == "BASKET_SENT"]
    assert len(notes) == 1 and len(notes[0].extra["orders"]) == 4
    await log.aclose()


async def test_a_refusal_mid_basket_stops_the_rest_and_says_which_legs_are_working(tmp_path):
    runtime, broker, log = world(tmp_path)
    original = broker.place
    calls = {"n": 0}

    async def refuse_second(request):
        calls["n"] += 1
        if calls["n"] == 2:
            broker.fail_next("insufficient margin")
        return await original(request)

    broker.place = refuse_second
    result = await runtime.baskets.send(condor(), "ayush")
    statuses = [v.status for v in result["sent"]]
    assert statuses[1] is OrderStatus.REJECTED and len(result["sent"]) == 2
    assert len(result["not_sent"]) == 2  # the two short legs never went
    assert "insufficient margin" in result["stopped"] and "2 legs not sent" in result["stopped"]
    await log.flush()
    assert [e.kind for e in log.events(account_id="acc") if e.kind.startswith("BASKET")] == [
        "BASKET_STOPPED"
    ]
    await log.aclose()


async def test_each_leg_sees_the_legs_before_it(tmp_path):
    runtime, broker, log = world(tmp_path, max_open_orders=3)
    checked = await runtime.baskets.check(condor(), "ayush")
    problems = [c.problem for c in checked.legs]
    assert sum(p is not None for p in problems) == 1  # the 4th leg would be the 4th working
    assert any("3 orders are already working" in (p or "") for p in problems)
    await log.aclose()


async def test_a_broker_that_quotes_margin_shows_the_hedge_benefit(tmp_path):
    runtime, broker, log = world(tmp_path, broker=QuotingBroker())
    checked = await runtime.baskets.check(condor(), "ayush")
    assert checked.margin == BasketMargin(40_000_00, 3_000_00)
    await log.aclose()


async def test_kite_basket_margin_request_and_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["query"] = request.url.path, dict(request.url.params)
        seen["body"] = json.loads(request.content)
        data = {"initial": {"total": 145230.5}, "final": {"total": 38650.25}, "orders": []}
        return httpx.Response(200, json={"status": "success", "data": data})

    http = KiteHttp("key", "tok", transport=httpx.MockTransport(handler))
    broker = KiteBroker(http, kite_user_id="AB1234")
    req = OrderRequest(
        "",
        "acc",
        "u",
        opt(25100, K.CALL).id,
        Side.SELL,
        75,
        OrderType.LIMIT,
        Product.NRML,
        price_paise=9990,
    )
    margin = await broker.basket_margin([req])
    assert margin == BasketMargin(14523050, 3865025)
    assert seen["path"] == "/margins/basket" and seen["query"] == {"consider_positions": "true"}
    assert seen["body"] == [
        {
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26OCT25100CE",
            "transaction_type": "SELL",
            "variety": "regular",
            "product": "NRML",
            "order_type": "LIMIT",
            "quantity": 75,
            "price": 99.9,
            "trigger_price": 0.0,
        }
    ]
    await http.aclose()


async def test_a_held_long_covers_a_later_short_of_the_same_type_and_expiry(tmp_path):
    runtime, broker, log = world(tmp_path)
    short = [LegIn(opt(25200, K.CALL).id, Side.SELL, 1)]
    assert not (await runtime.baskets.check(short, "ayush")).ok  # naked
    hold(runtime, {opt(25100, K.CALL).id: 75})  # long one lot
    assert (await runtime.baskets.check(short, "ayush")).ok  # now a call spread
    two = [LegIn(opt(25200, K.CALL).id, Side.SELL, 2)]
    assert not (await runtime.baskets.check(two, "ayush")).ok  # one lot covers one lot
    puts = [LegIn(opt(24800, K.PUT).id, Side.SELL, 1)]
    assert not (await runtime.baskets.check(puts, "ayush")).ok  # a call doesn't cover a put
    await log.aclose()


# --- an accepted buy is not protection: only a filled one is (#207) ---


async def test_short_legs_wait_for_the_protective_buys_to_fill(tmp_path):
    runtime, broker, log = world(tmp_path, broker=FillingBroker())
    result = await runtime.baskets.send(condor(), "ayush")
    statuses = {v.request.instrument_id[-7:]: v.status for v in result["sent"]}
    assert statuses["24800PE"] is OrderStatus.FILLED and statuses["25200CE"] is OrderStatus.FILLED
    assert result["stopped"] == "" and len(broker.placed) == 4
    await log.aclose()


async def test_unfilled_protection_sends_no_short_and_pulls_the_buys(tmp_path):
    runtime, broker, log = world(tmp_path, broker=FillingBroker())
    broker.fill_buys = False  # the buys are accepted but nobody sells to us
    result = await runtime.baskets.send(condor(), "ayush")
    assert [r.side for r in broker.placed] == [Side.BUY, Side.BUY]  # no short leg went
    assert len(broker.cancelled) == 2  # both unfilled buys pulled
    assert "not filled within 0.3 s" in result["stopped"]
    assert "short legs not sent" in result["stopped"] and "2 legs not sent" in result["stopped"]
    assert len(result["not_sent"]) == 2
    await log.aclose()


async def test_a_part_filled_then_cancelled_buy_stops_the_basket(tmp_path):
    runtime, broker, log = world(tmp_path, broker=FillingBroker(part=0))
    broker.fill_buys = False
    task = asyncio.create_task(runtime.baskets.send(condor(), "ayush"))
    while len(broker.placed) < 2:
        await asyncio.sleep(0.01)
    first = runtime.orders.orders()[0]
    runtime.orders.apply(
        OrderUpdate(first.client_order_id, first.broker_order_id, OrderStatus.CANCELLED, 0, 0)
    )
    result = await task
    assert "cancelled" in result["stopped"] and len(broker.placed) == 2
    await log.aclose()


async def test_a_working_buy_does_not_cover_a_short(tmp_path):
    runtime, broker, log = world(tmp_path)
    buy = await runtime.orders.submit(
        OrderRequest("", "acc", "ayush", opt(25100, K.CALL).id, Side.BUY, 75,
                     OrderType.LIMIT, Product.NRML, price_paise=9500)
    )  # fmt: skip
    assert buy.status is not OrderStatus.REJECTED  # working, not filled
    short = [LegIn(opt(25200, K.CALL).id, Side.SELL, 1)]
    assert not (await runtime.baskets.check(short, "ayush")).ok
    await log.aclose()


async def test_selling_the_long_that_protects_a_short_is_refused(tmp_path):
    runtime, broker, log = world(tmp_path)
    hold(runtime, {opt(25100, K.CALL).id: 75, opt(25200, K.CALL).id: -75})
    sell_long = [LegIn(opt(25100, K.CALL).id, Side.SELL, 1)]
    checked = await runtime.baskets.check(sell_long, "ayush")
    assert "not enabled" in checked.legs[0].problem  # it would leave the 25200 short naked
    hold(runtime, {opt(25100, K.CALL).id: 75})
    assert (await runtime.baskets.check(sell_long, "ayush")).ok  # nothing short: just closing
    await log.aclose()


async def test_growing_a_covered_short_past_its_cover_is_refused(tmp_path):
    from mocktrade.orders.model import OrderError

    runtime, broker, log = world(tmp_path)
    hold(runtime, {opt(25100, K.CALL).id: 75})
    short = await runtime.orders.submit(
        OrderRequest("", "acc", "ayush", opt(25200, K.CALL).id, Side.SELL, 75,
                     OrderType.LIMIT, Product.NRML, price_paise=12000)
    )  # fmt: skip
    assert short.status is not OrderStatus.REJECTED  # covered by the held long
    with pytest.raises(OrderError, match="not enabled"):
        await runtime.orders.modify(short.client_order_id, qty=150)
    await log.aclose()
