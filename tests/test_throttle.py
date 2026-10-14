"""The order throttle: exact rate limits, waiting vs refusing, and room for cancels."""

import asyncio
import random
from datetime import date, timedelta

import pytest

from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.orders.gate import Gates
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.throttle import DEFAULT_RULES, Kind, OrderThrottle, RateRule, Usage

MANUAL = OrderRequest(
    client_order_id="M1",
    account_id="acc-1",
    user_id="u-ayush",
    instrument_id="NFO:NIFTY26OCTFUT",
    side=Side.BUY,
    qty=75,
    order_type=OrderType.LIMIT,
    product=Product.NRML,
    price_paise=2501000,
)
ALGO = OrderRequest(**{**MANUAL.__dict__, "client_order_id": "A1", "algo_id": "ALG1"})


class Clock:
    """Time moves only when a test (or a throttle's sleep) moves it."""

    def __init__(self):
        self.t = 1000.0
        self.day = date(2026, 10, 14)
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def today(self) -> date:
        return self.day

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


class View:
    """Just enough of an OrderView for the throttle."""

    def __init__(self, client_order_id="M1"):
        self.client_order_id = client_order_id


@pytest.fixture
def clock():
    return Clock()


def throttle(clock, rules=DEFAULT_RULES, **kw) -> OrderThrottle:
    return OrderThrottle(rules, monotonic=clock.now, today=clock.today, sleep=clock.sleep, **kw)


# --- rules ---


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0, "cancel_limit": 1},
        {"limit": 5, "cancel_limit": 4},
        {"limit": 1, "cancel_limit": 1, "window_s": 0},
    ],
)
def test_rules_must_make_sense(kwargs):
    kwargs.setdefault("window_s", 1.0)
    with pytest.raises(ValueError):
        RateRule("x", **kwargs)


def test_defaults_stay_under_kite_and_sebi():
    second, minute, day = DEFAULT_RULES
    assert second.limit <= 8 and second.cancel_limit <= 10  # SEBI: 10 orders/s unregistered
    assert minute.cancel_limit <= 400 and day.limit < 5000
    assert day.counts == {Kind.NEW}


# --- manual orders are refused, never left hanging ---


async def test_ninth_order_in_a_second_is_refused_with_a_clear_reason(clock):
    t = throttle(clock)
    for _ in range(8):
        assert await t.admit_new(MANUAL) is None
    reason = await t.admit_new(MANUAL)
    assert "8 new orders per second" in reason and "try again in 1.0 s" in reason
    assert clock.slept == []


async def test_the_window_slides_exactly(clock):
    t = throttle(clock)
    for _ in range(8):
        await t.admit_new(MANUAL)
    clock.t += 0.99  # a token bucket would allow ~7 more here
    assert await t.admit_new(MANUAL) is not None
    clock.t += 0.01  # exactly one second after the first 8
    assert [await t.admit_new(MANUAL) for _ in range(9)].count(None) == 8


async def test_no_one_second_window_ever_holds_more_than_the_limit(clock):
    t = throttle(clock)
    rng = random.Random(7)
    sent = []
    for _ in range(3000):
        clock.t += rng.choice([0.0, 0.0, 0.01, 0.05, 0.13, 0.4])
        if await t.admit_new(MANUAL) is None:
            sent.append(clock.t)
    assert len(sent) > 300
    for i, start in enumerate(sent):
        inside = [s for s in sent[i:] if s < start + 1.0]
        assert len(inside) <= 8, start


async def test_minute_limit_applies_even_when_each_second_is_fine(clock):
    t = throttle(clock)
    for _ in range(350):
        assert await t.admit_new(MANUAL) is None
        clock.t += 0.15  # under 8 per second
    reason = await t.admit_new(MANUAL)
    assert "350 new orders per minute" in reason


# --- algos wait their turn ---


async def test_algo_orders_wait_instead_of_failing(clock):
    t = throttle(clock)
    for _ in range(8):
        await t.admit_new(ALGO)
    start = clock.t
    assert await t.admit_new(ALGO) is None
    assert clock.t - start == pytest.approx(1.0)


async def test_algo_gives_up_after_its_longest_wait(clock):
    t = throttle(clock, rules=(RateRule("per minute", 60.0, 2, 2),), max_wait_s=5)
    await t.admit_new(ALGO)
    await t.admit_new(ALGO)
    reason = await t.admit_new(ALGO)
    assert "per minute" in reason and "the most an algo order may wait" in reason


async def test_waiting_algo_orders_go_out_in_arrival_order(clock):
    t = throttle(clock, rules=(RateRule("per second", 1.0, 1, 1),))
    await t.admit_new(ALGO)
    out = []

    async def send(name):
        await t.admit_new(ALGO)
        out.append((name, clock.t))

    await asyncio.gather(send("first"), send("second"), send("third"))
    assert [name for name, _ in out] == ["first", "second", "third"]
    times = [when for _, when in out]
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(times, times[1:], strict=False))


# --- cancels always get out ---


async def test_cancels_have_room_reserved_above_orders(clock):
    t = throttle(clock)
    for _ in range(8):
        await t.admit_new(MANUAL)
    assert await t.admit_new(MANUAL) is not None  # orders are full...
    assert await t.admit_cancel(View()) is None  # ...cancels still go
    assert await t.admit_cancel(View()) is None
    assert clock.slept == []


async def test_cancels_wait_rather_than_fail(clock):
    t = throttle(clock, max_wait_s=0)
    for _ in range(10):
        await t.admit_cancel(View())
    assert await t.admit_cancel(View()) is None
    assert sum(clock.slept) == pytest.approx(1.0)


async def test_cancels_dont_use_the_daily_order_count(clock):
    t = throttle(clock, rules=(RateRule("per day", None, 1, 1, counts=frozenset({Kind.NEW})),))
    await t.admit_new(MANUAL)
    for _ in range(5):
        assert await t.admit_cancel(View()) is None


# --- the daily cap ---


async def test_daily_cap_refuses_even_algos_and_resets_at_midnight(clock):
    t = throttle(clock, rules=(RateRule("per day", None, 3, 3, counts=frozenset({Kind.NEW})),))
    for _ in range(3):
        assert await t.admit_new(ALGO) is None
    reason = await t.admit_new(ALGO)
    assert "daily limit of 3 new orders" in reason and clock.slept == []
    clock.day += timedelta(days=1)
    assert await t.admit_new(MANUAL) is None


async def test_default_daily_cap_blocks_order_4501(clock):
    t = throttle(clock)
    for i in range(4500):
        clock.t += 0.2
        assert await t.admit_new(MANUAL) is None, i
    clock.t += 0.2
    assert "daily limit of 4500" in await t.admit_new(MANUAL)


# --- modifications ---


async def test_the_26th_modification_is_refused_before_the_broker(clock):
    t = throttle(clock)
    for _ in range(25):
        clock.t += 0.2
        assert await t.admit_modify(View("X"), MANUAL) is None
    reason = await t.admit_modify(View("X"), MANUAL)
    assert "modified 25 times" in reason and "cancel it and place a new one" in reason
    assert await t.admit_modify(View("Y"), MANUAL) is None  # other orders unaffected
    clock.day += timedelta(days=1)
    assert await t.admit_modify(View("X"), MANUAL) is None


async def test_a_refused_modification_doesnt_count_toward_the_25(clock):
    t = throttle(clock, rules=(RateRule("per second", 1.0, 1, 1),))
    await t.admit_new(MANUAL)
    assert await t.admit_modify(View("X"), MANUAL) is not None  # rate-limited
    clock.t += 1.0
    for _ in range(25):  # all 25 are still available
        clock.t += 1.0
        assert await t.admit_modify(View("X"), MANUAL) is None


async def test_modifications_share_the_rate_with_new_orders(clock):
    t = throttle(clock)
    for _ in range(4):
        await t.admit_new(MANUAL)
        await t.admit_modify(View(), MANUAL)
    assert await t.admit_new(MANUAL) is not None


# --- for screens ---


async def test_usage_feeds_a_meter(clock):
    t = throttle(clock)
    for _ in range(3):
        await t.admit_new(MANUAL)
    await t.admit_cancel(View())
    assert t.usage() == [
        Usage("per second", 4, 8),
        Usage("per minute", 4, 350),
        Usage("per day", 3, 4500),
    ]
    clock.t += 1.0
    assert t.usage()[0] == Usage("per second", 0, 8)


# --- inside the order manager ---


async def test_throttle_as_the_order_managers_gate(clock, nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    broker = FakeBroker()
    t = throttle(clock, rules=(RateRule("per second", 1.0, 2, 3),))
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=Gates([t]))
    views = [await om.submit(MANUAL.__class__(**{**MANUAL.__dict__, "client_order_id": ""}))]
    views += [await om.submit(MANUAL.__class__(**{**MANUAL.__dict__, "client_order_id": ""}))]
    third = await om.submit(MANUAL.__class__(**{**MANUAL.__dict__, "client_order_id": ""}))
    assert [v.status for v in views] == [OrderStatus.PENDING, OrderStatus.PENDING]
    assert third.status is OrderStatus.REJECTED and "2 new orders per second" in third.reason
    assert len(broker.placed) == 2
    await om.cancel(views[0].client_order_id)  # the reserved room lets the cancel out
    assert broker.cancelled == [views[0].broker_order_id]


async def test_daily_cap_applies_again_on_the_next_day(clock):
    t = throttle(clock, rules=(RateRule("per day", None, 2, 2, counts=frozenset({Kind.NEW})),))
    for day in range(3):
        clock.day = date(2026, 10, 14) + timedelta(days=day)
        assert await t.admit_new(MANUAL) is None
        assert await t.admit_new(MANUAL) is None
        assert "daily limit" in await t.admit_new(MANUAL), day


async def test_wait_time_counts_cancels_that_used_the_reserved_room(clock):
    t = throttle(clock)
    start = clock.t
    for i in range(10):  # cancels at 0.0, 0.1, ... 0.9 s: the window holds 10 > 8
        clock.t = start + i / 10
        await t.admit_cancel(View())
    clock.t = start + 0.95
    reason = await t.admit_new(MANUAL)
    # 3 must leave before a new order fits: the third (sent at 0.2 s) leaves at 1.2 s
    assert "try again in 0.3 s" in reason  # 0.25 s rounds UP, so a retry then works
    clock.t = start + 1.2
    assert await t.admit_new(MANUAL) is None
