"""Positions and P&L: hand calculations, flips, exactness, replays, and the loss watch."""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest

from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
)
from mocktrade.domain.ticks import Tick
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.positions.book import PositionBook
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.loss_watch import AUTO, DailyLossWatch

FUT = "NFO:NIFTY26OCTFUT"
REL = "NSE:RELIANCE"
BUY, SELL = Side.BUY, Side.SELL
NRML, MIS = Product.NRML, Product.MIS
T0 = datetime(2026, 10, 14, 9, 15, tzinfo=UTC)


@pytest.fixture
def book():
    return PositionBook("acc-1", clock=lambda: T0)


def rupees(paise: int) -> float:
    return paise / 100


# --- hand calculations ---


def test_buy_then_sell_realises_the_difference(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10000)  # 75 @ ₹100
    book.apply_fill(FUT, NRML, SELL, 75, 10200)  # 75 @ ₹102
    position = book.get(FUT, NRML)
    assert rupees(position.realised_paise) == 150.0  # (102 - 100) × 75
    assert (position.qty, position.unrealised_paise) == (0, 0)
    assert (position.buy_qty, position.sell_qty) == (75, 75)
    assert (position.buy_value_paise, position.sell_value_paise) == (750000, 765000)


def test_unrealised_follows_the_last_price(book):
    book.apply_fill(FUT, NRML, BUY, 75, 2501000)
    assert book.get(FUT, NRML).unrealised_paise == 0  # no price yet
    book.mark(Tick(FUT, 2502000))  # +₹10
    assert rupees(book.get(FUT, NRML).unrealised_paise) == 750.0
    book.mark(Tick(FUT, 2499000))  # -₹20
    assert rupees(book.get(FUT, NRML).unrealised_paise) == -1500.0
    assert book.get(FUT, NRML).ltp_paise == 2499000


def test_short_positions_gain_when_price_falls(book):
    book.apply_fill(FUT, NRML, SELL, 75, 2501000)
    book.mark(Tick(FUT, 2500000))
    assert rupees(book.get(FUT, NRML).unrealised_paise) == 750.0
    book.apply_fill(FUT, NRML, BUY, 75, 2499000)
    assert rupees(book.get(FUT, NRML).realised_paise) == 1500.0


def test_partial_close_keeps_the_average_of_the_rest(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10005)  # ₹100.05
    book.apply_fill(FUT, NRML, BUY, 75, 10010)  # ₹100.10 -> average ₹100.075
    position = book.get(FUT, NRML)
    assert position.avg_price_paise == Fraction(200150, 20)  # 10007.5 paise, exactly
    book.apply_fill(FUT, NRML, SELL, 75, 10200)
    position = book.get(FUT, NRML)
    assert position.qty == 75 and position.avg_price_paise == Fraction(20015, 2)
    # (102.00 - 100.075) × 75 = ₹144.375 -> 14437.5 paise -> shown as 14438
    assert position.realised_paise == 14438


def test_flip_from_long_to_short_in_one_fill(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.apply_fill(FUT, NRML, SELL, 150, 10100)
    position = book.get(FUT, NRML)
    assert (position.qty, position.avg_price_paise) == (-75, 10100)  # new short at the fill
    assert rupees(position.realised_paise) == 75.0  # only the closed 75 realised
    book.mark(Tick(FUT, 10000))
    assert rupees(book.get(FUT, NRML).unrealised_paise) == 75.0  # short gains ₹1 × 75


def test_flip_from_short_to_long(book):
    book.apply_fill(FUT, NRML, SELL, 150, 10000)
    book.apply_fill(FUT, NRML, BUY, 225, 9900)
    position = book.get(FUT, NRML)
    assert (position.qty, position.avg_price_paise) == (75, 9900)
    assert rupees(position.realised_paise) == 150.0


def test_products_are_separate_positions_but_net_exposure_adds_up(book):
    book.apply_fill(FUT, MIS, BUY, 75, 10000)
    book.apply_fill(FUT, NRML, SELL, 150, 10000)
    assert book.get(FUT, MIS).qty == 75 and book.get(FUT, NRML).qty == -150
    assert book.net_qty(FUT) == -75
    assert book.net_qty(REL) == 0


def test_day_totals_across_instruments(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.apply_fill(FUT, NRML, SELL, 75, 10100)  # +75
    book.apply_fill(REL, Product.CNC, BUY, 10, 140000)
    book.mark(Tick(REL, 139000))  # -100
    assert rupees(book.realised_paise()) == 75.0
    assert rupees(book.unrealised_paise()) == -100.0
    assert rupees(book.day_pnl_paise()) == -25.0
    assert [(p.instrument_id, p.product) for p in book.positions()] == [
        (FUT, NRML),
        (REL, Product.CNC),
    ]


def test_bad_fills_are_refused(book):
    with pytest.raises(ValueError):
        book.apply_fill(FUT, NRML, BUY, 0, 10000)
    with pytest.raises(ValueError):
        book.apply_fill(FUT, NRML, BUY, 75, 0)


def test_rounding_is_symmetric_for_gains_and_losses(book):
    book.apply_fill(FUT, NRML, BUY, 1, 10001)
    book.apply_fill(FUT, NRML, BUY, 1, 10000)  # average 10000.5
    book.mark(Tick(FUT, 10000))
    assert book.get(FUT, NRML).unrealised_paise == -1  # -1.0 exactly
    book.apply_fill(FUT, NRML, SELL, 1, 10001)
    assert book.get(FUT, NRML).realised_paise == 1  # +0.5 -> 1 (away from zero)


# --- the independent check ---


@pytest.mark.parametrize("seed", range(20))
def test_pnl_always_equals_the_cash_flow_calculation(seed):
    """realised + unrealised must equal sells - buys + qty × last price, to the paisa."""
    rng = random.Random(seed)
    book = PositionBook("acc-1")
    price = 2500000
    for _ in range(200):
        price += rng.choice([-30, -20, -10, 0, 10, 20, 30])
        book.apply_fill(FUT, NRML, rng.choice([BUY, SELL]), 75 * rng.randint(1, 4), price)
        if rng.random() < 0.3:
            book.mark(Tick(FUT, price + rng.choice([-10, 0, 10])))
    book.mark(Tick(FUT, price))
    p = book.get(FUT, NRML)
    cash_flow = p.sell_value_paise - p.buy_value_paise + p.qty * price
    exact = book._positions[(FUT, NRML)]
    assert exact.realised + (price * p.qty - exact.cost) == cash_flow  # exact, no rounding
    assert abs(book.day_pnl_paise() - cash_flow) <= 1  # shown numbers within rounding


# --- fills from the order manager ---


@pytest.fixture
def wired(nifty_fut, book):
    store = InstrumentStore()
    store.add(nifty_fut)
    om = OrderManager("acc-1", FakeBroker(), store.get, ids=ClientIds("MT"))
    om.subscribe(book.on_order_event)
    return om


def request(side=BUY, qty=150, price=2501000) -> OrderRequest:
    return OrderRequest(
        client_order_id="",
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=FUT,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT,
        product=NRML,
        price_paise=price,
    )


async def test_fills_from_the_order_manager_build_the_position(wired, book):
    view = await wired.submit(request())
    cid = view.client_order_id
    wired.apply(OrderUpdate(cid, "F1", OrderStatus.PARTIAL, 75, 2501000, 75, 2501000))
    wired.apply(OrderUpdate(cid, "F1", OrderStatus.FILLED, 150, 2500995, 75, 2500990))
    position = book.get(FUT, NRML)
    assert (position.qty, position.buy_value_paise) == (150, 75 * 2501000 + 75 * 2500990)


async def test_a_replayed_fill_never_counts_twice(wired, book):
    view = await wired.submit(request())
    cid = view.client_order_id
    wired.apply(OrderUpdate(cid, "F1", OrderStatus.FILLED, 150, 2501000))
    fill_event = None

    def capture(event):
        nonlocal fill_event
        fill_event = event

    book2 = PositionBook("acc-1")
    stop = wired.subscribe(capture)
    second = await wired.submit(request(side=SELL, qty=75))
    wired.apply(OrderUpdate(second.client_order_id, "F2", OrderStatus.FILLED, 75, 2502000))
    stop()
    book2.on_order_event(fill_event)
    book2.on_order_event(fill_event)  # e.g. replayed after a restart
    assert book2.get(FUT, NRML).qty == -75
    assert book.get(FUT, NRML).qty == 75


def test_other_accounts_and_other_events_are_ignored(book):
    from mocktrade.orders.model import EventKind, Fill, OrderEvent

    book.on_order_event(OrderEvent(EventKind.SENT, T0, "acc-1", "X", None))
    book.on_order_event(
        OrderEvent(EventKind.FILL, T0, "acc-2", "X", None, fill=Fill("X", 75, 1, True))
    )
    assert book.positions() == []


# --- yesterday's positions ---


def test_carried_positions_start_today_at_zero_pnl(book):
    book.seed(FUT, NRML, 150, 2490000)  # yesterday's close ₹24,900
    assert book.day_pnl_paise() == 0
    assert (book.get(FUT, NRML).buy_qty, book.get(FUT, NRML).sell_qty) == (0, 0)
    book.mark(Tick(FUT, 2491000))
    assert rupees(book.day_pnl_paise()) == 1500.0  # ₹10 × 150 today
    with pytest.raises(ValueError, match="already"):
        book.seed(FUT, NRML, 75, 2490000)


# --- listeners ---


def test_listeners_hear_every_change_and_a_broken_one_is_harmless(book):
    heard = []
    book.subscribe(lambda view: 1 / 0)
    stop = book.subscribe(heard.append)
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.mark(Tick(FUT, 10100))
    book.mark(Tick(REL, 1))  # no position in it: nothing to say
    assert [(v.qty, v.unrealised_paise) for v in heard] == [(75, 0), (75, 7500)]
    stop()
    stop()
    book.mark(Tick(FUT, 10200))
    assert len(heard) == 2


# --- the daily loss watch ---


class Today:
    def __init__(self):
        self.day = date(2026, 10, 14)

    def __call__(self):
        return self.day


async def test_loss_watch_stops_trading_once_the_limit_is_reached(wired, book):
    switch = KillSwitch("account acc-1")
    today = Today()
    watch = DailyLossWatch(book.day_pnl_paise, 20_000_00, switch, [wired], today=today)
    book.subscribe(watch.check)
    working = await wired.submit(request(price=2400000))
    book.apply_fill(FUT, NRML, BUY, 150, 2501000)
    book.mark(Tick(FUT, 2490000))  # -₹16,500: fine
    assert not switch.engaged
    book.mark(Tick(FUT, 2487000))  # -₹21,000: stop
    await watch.settle()
    assert switch.engaged and switch.state.by == AUTO
    assert "₹21,000.00 lost (limit ₹20,000.00)" in switch.state.reason
    assert wired.get(working.client_order_id).pending.value == "CANCEL"


async def test_after_a_person_resets_it_the_watch_stays_quiet_that_day(wired, book):
    switch = KillSwitch("account acc-1")
    today = Today()
    watch = DailyLossWatch(book.day_pnl_paise, 1000, switch, [wired], today=today)
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.mark(Tick(FUT, 9900))
    assert watch.check()
    await watch.settle()
    switch.reset(by="u-ayush")  # to close the position
    assert not watch.check() and not switch.engaged
    today.day += timedelta(days=1)
    assert watch.check()  # a new day: armed again
    await watch.settle()


async def test_watch_does_nothing_if_someone_already_stopped_trading(wired, book):
    switch = KillSwitch("account acc-1")
    switch.engage("manual STOP", by="u-ayush")
    watch = DailyLossWatch(lambda: -(10**9), 1000, switch, [wired], today=Today())
    assert not watch.check()
    assert switch.state.reason == "manual STOP"
    with pytest.raises(ValueError):
        DailyLossWatch(lambda: 0, 0, switch, [wired])


def test_view_pnl_property(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.apply_fill(FUT, NRML, SELL, 25 * 3, 10100)
    view = replace(book.get(FUT, NRML), unrealised_paise=5)
    assert view.pnl_paise == view.realised_paise + 5


async def test_a_missed_fill_event_still_leaves_the_right_quantity(wired):
    """If the book missed the first fill, the second brings it up to the order's total."""
    late = PositionBook("acc-1")
    view = await wired.submit(request())
    cid = view.client_order_id
    wired.apply(OrderUpdate(cid, "F1", OrderStatus.PARTIAL, 75, 2501000, 75, 2501000))
    wired.subscribe(late.on_order_event)  # attached late: missed that fill
    wired.apply(OrderUpdate(cid, "F1", OrderStatus.FILLED, 150, 2501000, 75, 2501000))
    assert late.get(FUT, NRML).qty == 150


async def test_fills_for_another_account_are_ignored(wired):
    other = PositionBook("acc-2")
    wired.subscribe(other.on_order_event)
    view = await wired.submit(request())
    wired.apply(OrderUpdate(view.client_order_id, "F1", OrderStatus.FILLED, 150, 2501000))
    assert other.positions() == []


def test_a_zero_price_tick_never_marks_positions(book):
    book.apply_fill(FUT, NRML, BUY, 75, 10000)
    book.mark(Tick(FUT, 10100))
    book.mark(Tick(FUT, 0))  # pre-open or a bad tick
    assert book.get(FUT, NRML).ltp_paise == 10100


async def test_loss_watch_fires_at_exactly_the_limit(wired):
    switch = KillSwitch("account acc-1")
    watch = DailyLossWatch(lambda: -20_000_00, 20_000_00, switch, [wired], today=Today())
    assert watch.check()  # the risk gate already blocks at exactly the limit
    await watch.settle()
    assert switch.engaged


async def test_platform_marks_held_positions_from_live_prices(nifty_fut):
    import asyncio

    from mocktrade.domain.accounts import BrokerAccount
    from mocktrade.marketdata.service import MarketData
    from mocktrade.platform import Platform
    from mocktrade.risk.kill_switch import KillSwitch

    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    platform = Platform(store, md, KillSwitch("firm"))
    runtime = platform.add_account(
        BrokerAccount("acc-1", "paper", "P1", "u"), FakeBroker(), ids=ClientIds("MT")
    )
    runtime.positions.apply_fill(FUT, NRML, BUY, 75, 2501000)
    task = asyncio.create_task(platform.run(reconcile=False))
    for _ in range(20):
        await asyncio.sleep(0)
    md.on_tick(Tick(FUT, 2502000))
    for _ in range(50):
        if runtime.positions.get(FUT, NRML).unrealised_paise:
            break
        await asyncio.sleep(0.01)
    assert runtime.positions.get(FUT, NRML).unrealised_paise == 75_000
    task.cancel()
