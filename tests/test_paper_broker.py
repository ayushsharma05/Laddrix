"""The paper broker, scenario by scenario.

Instrument used throughout: NIFTY future, tick 10 paise, lot 75.
Prices below are in paise (2501000 = 25,010.00).
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.broker.paper import PaperBroker, _base36
from mocktrade.broker.rules import MAX_MODIFICATIONS_PER_ORDER, protected_limit
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
    Validity,
)
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore

FUT = "NFO:NIFTY26OCTFUT"


def tick(ltp, bids=(), asks=(), volume=None, instrument_id=FUT) -> Tick:
    return Tick(
        instrument_id=instrument_id,
        ltp_paise=ltp,
        volume=volume,
        bids=tuple(DepthLevel(p, q, 1) for p, q in bids),
        asks=tuple(DepthLevel(p, q, 1) for p, q in asks),
    )


BOOK = tick(
    2501000,
    bids=[(2501000, 150), (2500990, 300), (2500980, 75)],
    asks=[(2501010, 75), (2501020, 150), (2501030, 600)],
    volume=10_000,
)


@pytest.fixture
def market(nifty_fut, reliance):
    store = InstrumentStore()
    store.add(nifty_fut)
    store.add(reliance)
    return store, MarketData()


@pytest.fixture
def broker(market):
    store, md = market
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    b = PaperBroker(md, store, latency_s=0.08, sleep=fake_sleep)
    b.sleeps = sleeps
    return b


def order(cid="MT1", side=Side.BUY, qty=75, price=2501000, **changes) -> OrderRequest:
    base = OrderRequest(
        client_order_id=cid,
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=FUT,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT,
        product=Product.NRML,
        price_paise=price,
    )
    return replace(base, **changes)


def market_order(side=Side.BUY, qty=75, protection="2", **changes) -> OrderRequest:
    return order(
        side=side,
        qty=qty,
        price=None,
        order_type=OrderType.MARKET,
        market_protection=Decimal(protection),
        **changes,
    )


async def drain(b: PaperBroker) -> list:
    """Everything the broker has reported so far."""
    out, stream = [], b.updates()
    while True:
        try:
            out.append(await asyncio.wait_for(anext(stream), 0.01))
        except TimeoutError:
            return out


async def place(b, md, request, book=BOOK):
    if book is not None:
        md.on_tick(book)
    broker_id = await b.place(request)
    await b.settle()
    return broker_id


# --- placing ----------------------------------------------------------------------------


def test_paper_broker_follows_the_broker_interface(broker):
    assert isinstance(broker, Broker)


async def test_ids_are_sequential_and_ack_waits_for_latency(broker, market):
    _, md = market
    first = await place(broker, md, order(price=2500000))
    second = await place(broker, md, order(cid="MT2", price=2500000))
    assert (first, second) == (f"P{broker.run_id}-1", f"P{broker.run_id}-2")
    assert broker.sleeps == [0.08, 0.08]
    statuses = [u.status for u in await drain(broker)]
    assert statuses == [OrderStatus.OPEN, OrderStatus.OPEN]


@pytest.mark.parametrize(
    "request_",
    [
        order(qty=50),  # not a whole lot
        order(price=2501005),  # off the tick grid
        order(instrument_id="NSE:NOPE"),  # unknown
        order(order_type=OrderType.MARKET),  # no market protection
    ],
)
async def test_invalid_requests_are_refused_before_acceptance(broker, request_):
    with pytest.raises(BrokerError):
        await broker.place(request_)
    assert await broker.orders() == []


# --- taking liquidity -------------------------------------------------------------------


async def test_marketable_buy_walks_the_asks_at_each_levels_price(broker, market):
    _, md = market
    await place(broker, md, order(qty=225, price=2501020))
    updates = await drain(broker)
    fills = [(u.last_fill_qty, u.last_fill_price_paise) for u in updates if u.last_fill_qty]
    assert fills == [(75, 2501010), (150, 2501020)]
    assert updates[-1].status is OrderStatus.FILLED
    assert updates[-1].avg_price_paise == (75 * 2501010 + 150 * 2501020) // 225


async def test_never_takes_more_than_the_visible_quantity(broker, market):
    _, md = market
    await place(broker, md, order(qty=900, price=2501010))
    last = (await drain(broker))[-1]
    assert (last.status, last.filled_qty) == (OrderStatus.PARTIAL, 75)


async def test_marketable_sell_hits_the_bids(broker, market):
    _, md = market
    await place(broker, md, order(side=Side.SELL, qty=225, price=2500990))
    fills = [(u.last_fill_qty, u.last_fill_price_paise) for u in await drain(broker)]
    assert (150, 2501000) in fills and (75, 2500990) in fills


async def test_two_orders_in_the_same_tick_share_the_depth(broker, market):
    store, md = market
    md.on_tick(BOOK)
    await broker.place(order(cid="A", qty=75, price=2501010))
    await broker.place(order(cid="B", qty=75, price=2501010))
    await broker.settle()
    filled = {u.client_order_id: u.filled_qty for u in await drain(broker)}
    assert filled == {"A": 75, "B": 0}


async def test_ioc_cancels_what_cant_fill_now(broker, market):
    _, md = market
    await place(broker, md, order(qty=150, price=2501010, validity=Validity.IOC))
    last = (await drain(broker))[-1]
    assert (last.status, last.filled_qty) == (OrderStatus.CANCELLED, 75)
    assert "IOC" in last.reason


async def test_ioc_that_fills_completely_is_simply_filled(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2501010, validity=Validity.IOC))
    assert (await drain(broker))[-1].status is OrderStatus.FILLED


# --- market orders and protection -------------------------------------------------------


@pytest.mark.parametrize(
    "side, ltp, pct, expected",
    [
        (Side.BUY, 2501000, "2", 2551020),  # 25,510.20 is on the grid already
        (Side.BUY, 2501005, "2", 2551020),  # 25,510.251 -> rounds DOWN for a buy
        (Side.SELL, 2501005, "2", 2450990),  # 24,509.849 -> rounds UP for a sell
        (Side.BUY, 2501000, "-1", 2551020),  # -1 means automatic = 2%
        (Side.SELL, 2501000, "0.5", 2488500),  # fractions of a percent are allowed
        (Side.SELL, 10, "100", 10),  # never below one tick
    ],
)
def test_protected_limit_stays_inside_the_protection(nifty_fut, side, ltp, pct, expected):
    assert protected_limit(nifty_fut, side, ltp, Decimal(pct)) == expected


async def test_market_buy_fills_within_protection_only(broker, market):
    _, md = market
    far = tick(2501000, asks=[(2501010, 75), (2700000, 75)])
    await place(broker, md, market_order(qty=150, protection="2"), book=far)
    last = (await drain(broker))[-1]
    assert last.filled_qty == 75  # the 27,000 offer is beyond 2%
    assert last.status is OrderStatus.PARTIAL


async def test_market_order_with_no_offers_rests_at_its_protected_price(broker, market):
    _, md = market
    no_offers = tick(2501000, bids=[(2501000, 150)], volume=10_000)
    broker_id = await place(broker, md, market_order(), book=no_offers)
    (view,) = await broker.orders()
    assert (view.broker_order_id, view.status) == (broker_id, OrderStatus.OPEN)
    broker.on_tick(tick(2501000, bids=[(2501000, 150)], asks=[(2501500, 75)], volume=10_000))
    last = (await drain(broker))[-1]
    assert (last.status, last.last_fill_price_paise) == (OrderStatus.FILLED, 2551020)


async def test_market_order_without_any_price_is_rejected(broker, market):
    _, md = market
    await place(broker, md, market_order(), book=None)
    last = (await drain(broker))[-1]
    assert last.status is OrderStatus.REJECTED and "no market price" in last.reason


# --- resting orders and the queue ---------------------------------------------------------


async def test_resting_order_waits_behind_the_visible_queue(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2501000))  # 150 already bid at 25,010.00
    await drain(broker)
    md.on_tick(tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_100))
    broker.on_tick(md.latest(FUT))
    assert await drain(broker) == []  # 100 traded: still 50 ahead of us
    broker.on_tick(tick(2501000, bids=[(2501000, 50)], asks=[(2501010, 75)], volume=10_200))
    last = (await drain(broker))[-1]
    assert (last.last_fill_qty, last.last_fill_price_paise) == (50, 2501000)
    assert last.status is OrderStatus.PARTIAL


async def test_price_trading_through_fills_completely_at_our_price(broker, market):
    _, md = market
    await place(broker, md, order(qty=150, price=2500990))
    await drain(broker)
    broker.on_tick(tick(2500980, bids=[(2500980, 75)], asks=[(2500990, 75)], volume=10_050))
    last = (await drain(broker))[-1]
    assert (last.status, last.avg_price_paise) == (OrderStatus.FILLED, 2500990)


async def test_resting_order_gets_no_price_improvement(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2500990))
    await drain(broker)
    broker.on_tick(tick(2500990, bids=[(2500980, 75)], asks=[(2500970, 300)], volume=10_000))
    last = (await drain(broker))[-1]
    assert last.last_fill_price_paise == 2500990  # not the better 25,009.70 offer


WIDE = tick(2501000, bids=[(2501000, 150)], asks=[(2501030, 75)], volume=10_000)


async def test_improving_the_best_bid_puts_us_first_in_line(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2501010), book=WIDE)  # inside the spread
    await drain(broker)
    broker.on_tick(tick(2501010, bids=[(2501010, 75)], asks=[(2501030, 75)], volume=10_075))
    last = (await drain(broker))[-1]
    assert (last.status, last.last_fill_price_paise) == (OrderStatus.FILLED, 2501010)


async def test_last_price_at_our_level_without_volume_never_fills(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2501010), book=WIDE)
    await drain(broker)
    broker.on_tick(tick(2501010))  # LTP-only tick: no depth, no volume
    assert await drain(broker) == []
    broker.on_tick(tick(2501000))  # trades through: certain we were filled
    assert (await drain(broker))[-1].status is OrderStatus.FILLED


async def test_volume_traded_before_we_joined_does_not_count(broker, market):
    _, md = market
    md.on_tick(WIDE)
    broker.on_tick(WIDE)  # the broker has seen volume 10,000
    busy = tick(2501010, bids=[(2501010, 75)], asks=[(2501030, 75)], volume=10_500)
    await place(broker, md, order(qty=75, price=2501010), book=busy)  # 75 ahead of us
    broker.on_tick(busy)  # same tick again: its 500 traded before we arrived
    assert [u.status for u in await drain(broker)] == [OrderStatus.OPEN]


async def test_our_orders_share_traded_volume_in_arrival_order(broker, market):
    _, md = market
    front = tick(2501000, bids=[(2500990, 75)], asks=[(2501010, 75)], volume=10_000)
    await place(broker, md, order(cid="A", qty=75, price=2501000), book=front)
    await place(broker, md, order(cid="B", qty=75, price=2501000), book=None)
    await drain(broker)
    broker.on_tick(tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_075))
    filled = {u.client_order_id: u.filled_qty for u in await drain(broker)}
    assert filled == {"A": 75}  # 75 traded: only enough for the first of our two orders


async def test_price_outside_visible_depth_waits_until_it_comes_into_view(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2500000))  # far below the 5 visible bids
    await drain(broker)
    broker.on_tick(tick(2500000, bids=[(2500000, 300)], asks=[(2500010, 75)], volume=10_100))
    assert await drain(broker) == []  # first sight of our level: 300 ahead of us
    broker.on_tick(tick(2500000, bids=[(2500000, 75)], asks=[(2500010, 75)], volume=10_475))
    assert (await drain(broker))[-1].filled_qty == 75


# --- stop orders ------------------------------------------------------------------------


async def test_buy_stop_waits_for_its_trigger_then_works_as_a_limit(broker, market):
    _, md = market
    stop = order(order_type=OrderType.SL, price=2502000, trigger_paise=2501500)
    await place(broker, md, stop)
    assert [u.status for u in await drain(broker)] == [OrderStatus.OPEN]  # not triggered
    broker.on_tick(tick(2501500, asks=[(2501600, 75)], volume=10_000))
    last = (await drain(broker))[-1]
    # once triggered it arrives like a new order, so it trades at the offer, not at 25,020.00
    assert (last.status, last.last_fill_price_paise) == (OrderStatus.FILLED, 2501600)


async def test_stop_that_cannot_trade_yet_waits_in_the_queue(broker, market):
    _, md = market
    stop = order(order_type=OrderType.SL, price=2501500, trigger_paise=2501500)
    await place(broker, md, stop)
    await drain(broker)
    broker.on_tick(tick(2501500, bids=[(2501500, 75)], asks=[(2501600, 75)], volume=10_000))
    assert await drain(broker) == []  # triggered, but nobody sells at 25,015.00 yet
    broker.on_tick(tick(2501400, bids=[(2501400, 75)], asks=[(2501500, 75)], volume=10_100))
    assert (await drain(broker))[-1].status is OrderStatus.FILLED


async def test_sell_stop_market_uses_protection_after_trigger(broker, market):
    _, md = market
    stop = order(
        side=Side.SELL,
        order_type=OrderType.SL_M,
        price=None,
        trigger_paise=2500500,
        market_protection=Decimal("1"),
    )
    await place(broker, md, stop)
    await drain(broker)
    broker.on_tick(tick(2500500, bids=[(2500400, 75), (2400000, 75)], volume=10_000))
    last = (await drain(broker))[-1]
    assert (last.filled_qty, last.avg_price_paise) == (75, 2500400)  # 24,000 is beyond 1%


@pytest.mark.parametrize(
    ("side", "trigger"),
    [(Side.BUY, 2501000), (Side.BUY, 2500000), (Side.SELL, 2501000), (Side.SELL, 2502000)],
)
async def test_a_stop_the_market_already_passed_is_refused_like_kite(broker, market, side, trigger):
    """LTP is 25,010.00: a buy stop must trigger above it, a sell stop below it."""
    _, md = market
    md.on_tick(BOOK)
    stop = order(
        side=side,
        order_type=OrderType.SL_M,
        price=None,
        trigger_paise=trigger,
        market_protection=Decimal("-1"),
    )
    with pytest.raises(BrokerError, match="last traded price"):
        await broker.place(stop)


async def test_moving_a_stop_trigger_past_the_market_is_refused(broker, market):
    _, md = market
    stop = order(order_type=OrderType.SL, price=2502000, trigger_paise=2501500)
    broker_id = await place(broker, md, stop)
    with pytest.raises(BrokerError, match="last traded price"):
        await broker.modify(broker_id, trigger_paise=2500900, price_paise=2501000)


# --- modify -----------------------------------------------------------------------------


async def test_price_change_sends_the_order_to_the_back_of_the_queue(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(qty=75, price=2500990))  # 300 ahead
    await broker.modify(broker_id, price_paise=2501000)  # now 150 ahead at the new price
    await broker.settle()
    await drain(broker)
    broker.on_tick(tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_150))
    assert await drain(broker) == []
    broker.on_tick(tick(2501000, bids=[(2501000, 75)], asks=[(2501010, 75)], volume=10_225))
    assert (await drain(broker))[-1].filled_qty == 75


@pytest.mark.parametrize(
    "new_qty, fills_next",
    [
        (75, 25),  # lowering keeps our place: only 50 left ahead, so 25 of the 75 fill
        (225, 0),  # raising loses it: 150 are ahead of us again
    ],
)
async def test_lowering_quantity_keeps_queue_place_raising_it_loses_it(
    broker, market, new_qty, fills_next
):
    _, md = market
    broker_id = await place(broker, md, order(qty=150, price=2501000))  # 150 ahead
    # 100 trade (50 still ahead of us); 100 newcomers join behind us
    moved = tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_100)
    md.on_tick(moved)
    broker.on_tick(moved)
    await broker.modify(broker_id, qty=new_qty)
    await broker.settle()
    await drain(broker)
    broker.on_tick(tick(2501000, bids=[(2501000, 75)], asks=[(2501010, 75)], volume=10_175))
    fills = [u.last_fill_qty for u in await drain(broker)]
    assert sum(fills) == fills_next


async def test_modify_before_acknowledgement_is_used_on_acceptance(market):
    store, md = market
    gate = asyncio.Event()

    async def slow_sleep(_):
        await gate.wait()

    b = PaperBroker(md, store, sleep=slow_sleep)
    md.on_tick(BOOK)
    broker_id = await b.place(order(qty=75, price=2500000))
    await b.modify(broker_id, price_paise=2501010)  # now crosses the 25,010.10 offer
    gate.set()
    await b.settle()
    last = (await drain(b))[-1]
    assert (last.status, last.last_fill_price_paise) == (OrderStatus.FILLED, 2501010)


async def test_modify_into_a_crossing_price_trades_like_a_new_order(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(qty=75, price=2500990))
    await broker.modify(broker_id, price_paise=2501010)
    await broker.settle()
    last = (await drain(broker))[-1]
    assert (last.status, last.last_fill_price_paise) == (OrderStatus.FILLED, 2501010)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"qty": 50}, "lot"),
        ({"price_paise": 2501005}, "tick"),
    ],
)
async def test_invalid_modifications_are_refused(broker, market, changes, message):
    _, md = market
    broker_id = await place(broker, md, order(price=2500000))
    with pytest.raises(BrokerError, match=message):
        await broker.modify(broker_id, **changes)


async def test_quantity_must_stay_above_what_already_filled(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(qty=150, price=2501010))  # 75 fills at once
    with pytest.raises(BrokerError, match="filled"):
        await broker.modify(broker_id, qty=75)


async def test_modification_cap(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(price=2500000))
    for i in range(MAX_MODIFICATIONS_PER_ORDER):
        await broker.modify(broker_id, price_paise=2500000 - 10 * (i + 1))
    with pytest.raises(BrokerError, match="cancel and re-place"):
        await broker.modify(broker_id, price_paise=2400000)


async def test_finished_orders_cannot_be_modified_or_cancelled(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(qty=75, price=2501010))  # fills at once
    with pytest.raises(BrokerError, match="filled"):
        await broker.modify(broker_id, price_paise=2501000)
    with pytest.raises(BrokerError, match="filled"):
        await broker.cancel(broker_id)
    with pytest.raises(BrokerError, match="unknown"):
        await broker.cancel("P999")


# --- cancel -----------------------------------------------------------------------------


async def test_cancel_reports_cancelled_with_reason(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(price=2500000))
    await broker.cancel(broker_id)
    await broker.settle()
    last = (await drain(broker))[-1]
    assert (last.status, last.reason) == (OrderStatus.CANCELLED, "cancelled by user")


async def test_cancel_overtaking_the_acknowledgement_never_reports_open(market):
    """If the cancel is processed first, a late acceptance must not revive the order."""
    store, md = market
    gate = asyncio.Event()
    calls = 0

    async def place_is_slow(_):
        nonlocal calls
        calls += 1
        if calls == 1:  # the place acknowledgement waits; the cancel goes straight through
            await gate.wait()

    b = PaperBroker(md, store, sleep=place_is_slow)
    md.on_tick(BOOK)
    broker_id = await b.place(order(price=2500000))
    await b.cancel(broker_id)
    for _ in range(5):
        await asyncio.sleep(0)  # let the cancel finish while the place is still waiting
    gate.set()
    await b.settle()
    statuses = [u.status for u in await drain(b)]
    assert statuses == [OrderStatus.CANCELLED]


async def test_cancel_after_acknowledgement_reports_open_then_cancelled(broker, market):
    _, md = market
    broker_id = await place(broker, md, order(price=2500000))
    await broker.cancel(broker_id)
    await broker.settle()
    assert [u.status for u in await drain(broker)] == [OrderStatus.OPEN, OrderStatus.CANCELLED]


# --- views used for reconciliation ------------------------------------------------------


async def test_orders_and_positions_views(broker, market):
    _, md = market
    await place(broker, md, order(cid="B1", qty=150, price=2501020))  # buys 75@..10, 75@..20
    await place(broker, md, order(cid="S1", side=Side.SELL, qty=75, price=2500990))
    views = {u.client_order_id: u.status for u in await broker.orders()}
    assert views == {"B1": OrderStatus.FILLED, "S1": OrderStatus.FILLED}
    (position,) = await broker.positions()
    assert (position.instrument_id, position.qty) == (FUT, 75)
    assert position.avg_price_paise == (75 * 2501010 + 75 * 2501020) // 150


async def test_flat_position_has_zero_average(broker, market):
    _, md = market
    await place(broker, md, order(cid="B", qty=75, price=2501010))
    await place(broker, md, order(cid="S", side=Side.SELL, qty=75, price=2501000))
    (position,) = await broker.positions()
    assert (position.qty, position.avg_price_paise) == (0, 0)


# --- running live -----------------------------------------------------------------------


async def test_live_ticks_from_the_market_data_cache_drive_fills(market):
    store, md = market
    b = PaperBroker(md, store, latency_s=0)
    b.start()
    md.on_tick(BOOK)
    await b.place(order(qty=75, price=2500990))
    await b.settle()
    md.on_tick(tick(2500980, bids=[(2500980, 75)], asks=[(2500990, 75)], volume=10_100))
    for _ in range(50):
        await asyncio.sleep(0)
    statuses = [u.status for u in await drain(b)]
    assert statuses[-1] is OrderStatus.FILLED
    await b.aclose()
    assert b._runner is None


async def test_paper_orders_never_touch_another_instrument(broker, market):
    _, md = market
    await place(broker, md, order(price=2500000))
    await drain(broker)
    broker.on_tick(tick(100, instrument_id="NSE:RELIANCE", volume=5))
    broker.on_tick(tick(90, instrument_id="NSE:RELIANCE", volume=500))
    assert await drain(broker) == []


async def test_a_late_older_tick_does_not_make_volume_count_twice(broker, market):
    _, md = market
    await place(broker, md, order(qty=75, price=2501000))  # 150 ahead, volume 10,000
    await drain(broker)
    broker.on_tick(tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_100))
    late = tick(2501000, bids=[(2501000, 150)], asks=[(2501010, 75)], volume=10_000)
    broker.on_tick(late)  # an older tick delivered late
    broker.on_tick(tick(2501000, bids=[(2501000, 50)], asks=[(2501010, 75)], volume=10_150))
    # really 150 traded (exactly the queue ahead); counting from the late tick would say 250
    assert await drain(broker) == []


# --- ids across a restart (#419) ------------------------------------------------------------


async def test_ids_never_repeat_across_a_restart_so_old_trades_never_correct_new_fills(market):
    """Found by the spread ladder's browser check: after a restart the first new order was
    "P1" again, the day's adopted trades held an older "P1"/"T1" (another price), and the
    trade check took that trade as the new order's and "corrected" it."""
    from mocktrade.orders.trade_check import ledger

    store, md = market
    first_start = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
    md.on_tick(Tick(FUT, 2514570, bids=(DepthLevel(2514560, 750, 3),),
                    asks=(DepthLevel(2514570, 750, 3),)))  # fmt: skip
    before = PaperBroker(md, store, latency_s=0.0, clock=lambda: first_start)
    before.start()
    old = await before.place(order(cid="MT1", price=2514570))  # the morning: it fills
    await before.settle()
    earlier = await before.trades()
    await before.aclose()

    later = first_start + timedelta(minutes=7)
    after = PaperBroker(md, store, latency_s=0.0, clock=lambda: later)  # the restart
    after.adopt(trades=earlier)  # today's earlier trades come back
    after.start()
    try:
        md.on_tick(Tick(FUT, 2500960, bids=(DepthLevel(2500950, 750, 3),),
                        asks=(DepthLevel(2500960, 750, 3),)))  # fmt: skip
        new = await after.place(order(cid="MT9", price=2500960))
        await after.settle()
        trades = await after.trades()
        assert new != old
        assert len({t.trade_id for t in trades}) == len(trades) == 2  # no trade id twice
        assert ledger(trades)[new] == (75, 75 * 2500960)  # its own fill, at its own price
        assert ledger(trades)[old] == (75, 75 * 2514570)
    finally:
        await after.aclose()


def test_a_replay_with_a_fixed_clock_gets_the_same_ids_every_run(market):
    store, md = market
    start = datetime(2026, 10, 14, 3, 45, tzinfo=UTC)
    runs = [PaperBroker(md, store, clock=lambda: start).run_id for _ in range(2)]
    assert runs[0] == runs[1] and runs[0] == _base36(int(start.timestamp()))
    assert [_base36(n) for n in (0, 35, 36, 1792834471)] == ["0", "z", "10", "tnenxj"]
