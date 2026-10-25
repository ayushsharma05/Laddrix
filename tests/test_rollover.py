"""A new trading day without a restart (#266).

The failure before: a server running from one day into the next kept yesterday's realised
P&L in today's: the daily loss limit (and every check that reads day P&L) judged today by
yesterday's losses, and yesterday's unfilled day orders still counted as working today."""

from datetime import timedelta

import pytest
from test_mtm import Clock, move
from test_portfolio import FUT, Paper

from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side

S = OrderStatus


@pytest.fixture
async def days(nifty_fut, tmp_path):
    clock = Clock(10, 0)
    p = Paper(nifty_fut, tmp_path, clock=clock)
    p.clock = clock
    yield p
    await p.stop()


def next_morning(p, hh=8, mm=45):
    p.clock.now = p.clock.now.replace(hour=0, minute=0) + timedelta(days=1, hours=hh - 5,
                                                                    minutes=mm - 30)  # fmt: skip


async def test_day_two_starts_at_zero_and_overnight_positions_keep_their_economics(days):
    p = days
    await p.buy(FUT, 150, 2501010, Product.NRML)  # carried overnight
    await p.buy(FUT, 75, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2400000, Product.MIS, side=Side.SELL)  # MIS closed the same day
    await move(p, 2490000)
    assert p.rt.positions.day_pnl_paise() < -10_000_00  # a bad day 1
    next_morning(p)
    done = p.platform.roll_day()
    assert done["acc"].startswith("new trading day 2026-10-17: 1 position(s) carried")
    assert p.rt.positions.day_pnl_paise() == 0  # day 2 starts clean
    (nrml,) = [v for v in p.rt.positions.positions() if v.qty]
    assert (nrml.product, nrml.qty, nrml.realised_paise, nrml.buy_qty) == (Product.NRML, 150, 0, 0)
    assert nrml.avg_price_paise == 2490000  # valued at the last price of day 1…
    assert nrml.carried_avg_paise == 2501010  # …and still knows what it was bought at
    await move(p, 2491000)
    assert p.rt.positions.day_pnl_paise() == 150 * 1000  # only today's move counts


async def test_the_daily_loss_limit_judges_today_not_yesterday(days):
    p = days
    await p.buy(FUT, 300, 2501010, Product.MIS)
    await move(p, 2400000)
    await p.buy(FUT, 300, 2390000, Product.MIS, side=Side.SELL)  # −₹3 lakh: past ₹20,000
    opening = OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT,
                           Product.NRML, price_paise=2400000)  # fmt: skip
    refused = await p.rt.orders.submit(opening)
    assert "daily loss limit reached" in refused.reason
    next_morning(p)
    p.platform.roll_day()
    accepted = await p.rt.orders.submit(opening)
    assert accepted.status is not S.REJECTED, accepted.reason


async def test_yesterdays_day_orders_expire_and_stop_counting(days):
    p = days
    resting = await p.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75,
                                                    OrderType.LIMIT, Product.NRML,
                                                    price_paise=2480000))  # fmt: skip
    assert [o.client_order_id for o in p.rt.orders.working()] == [resting.client_order_id]
    next_morning(p)
    p.platform.roll_day()
    ended = p.rt.orders.get(resting.client_order_id)
    assert ended.status is S.CANCELLED
    assert ended.reason == "expired: a day order ends with its trading day"
    assert p.rt.orders.working() == []


async def test_a_delivery_buy_becomes_a_holding_and_flat_rows_go(days):
    p = days
    await p.buy(FUT.replace("NFO:NIFTY26OCTFUT", "NSE:RELIANCE"), 10, 250010, Product.CNC)
    await p.buy(FUT, 75, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2400000, Product.MIS, side=Side.SELL)  # flat by the close
    next_morning(p)
    assert p.platform.roll_day()["acc"].startswith("new trading day 2026-10-17: 0 position(s)")
    assert p.rt.positions.positions() == []


async def test_it_runs_once_a_day_and_a_restart_day_is_already_today(days):
    p = days
    assert p.platform.roll_day() == {}  # started today: today's state is already built
    next_morning(p)
    assert list(p.platform.roll_day()) == ["acc"]
    assert p.platform.roll_day() == {}  # the second run that morning changes nothing
    await p.log.flush()
    notes = [e.detail for e in p.log.events(account_id="acc") if e.kind == "SESSION_ROLLED"]
    assert len(notes) == 1


async def test_the_days_charges_start_again(days):
    p = days
    await p.buy(FUT, 75, 2501010, Product.MIS)
    assert p.rt.charges.bill().total_paise > 0
    next_morning(p)
    p.platform.roll_day()
    assert p.rt.charges.bill().total_paise == 0


def test_a_holiday_morning_does_not_roll():
    """The rollover is a daily job: the scheduler runs those on trading days only."""
    from datetime import date

    from laddrix.ops.scheduler import Scheduler

    s = Scheduler(holidays=frozenset({date(2026, 11, 10)}))
    assert not s.is_trading_day(date(2026, 11, 10)) and not s.is_trading_day(date(2026, 10, 18))


async def test_an_order_placed_this_morning_before_the_roll_is_not_expired(days):
    p = days
    next_morning(p, 8, 30)
    early = await p.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75,
                                                  OrderType.LIMIT, Product.NRML,
                                                  price_paise=2480000))  # fmt: skip
    p.clock.now += timedelta(minutes=15)  # 08:45: the roll
    p.platform.roll_day()
    assert p.rt.orders.get(early.client_order_id).status is not S.CANCELLED  # today's: kept
