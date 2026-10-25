"""Changes and cancels in doubt (#260): one change at a time per order, so answers can't undo a
newer change; a change with no answer counts the larger quantity until the broker's own terms
say; an older streamed update can't undo a confirmed change; a cancel with no answer stays
"cancel on its way" and is checked; fills survive every race; and risk is checked again
after the throttle made an order wait."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from laddrix.broker.base import BrokerError
from laddrix.broker.fake import FakeBroker
from laddrix.broker.kite.orders import to_update
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from laddrix.orders.gate import Gates
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind, OrderError, Pending
from laddrix.refdata.store import InstrumentStore

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Broker(FakeBroker):
    """Its modify and cancel can be held open, fail, or get no answer."""

    def __init__(self):
        super().__init__()
        self.hold: asyncio.Event | None = None
        self.no_answer = False
        self.refuse: str | None = None
        self.listing: list[OrderUpdate] = []

    async def modify(self, broker_order_id, **changes):
        if self.hold is not None:
            await self.hold.wait()
        if self.refuse:
            raise BrokerError(self.refuse)
        if self.no_answer:
            raise TimeoutError("read timed out")
        await super().modify(broker_order_id, **changes)

    async def cancel(self, broker_order_id):
        if self.refuse:
            raise BrokerError(self.refuse)
        if self.no_answer:
            raise TimeoutError("read timed out")
        await super().cancel(broker_order_id)

    async def orders(self):
        return list(self.listing)


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    clock, broker, events = Clock(), Broker(), []

    async def sleep(seconds):
        clock.advance(seconds)

    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock,
                      follow_up_s=(), sleep=sleep)  # fmt: skip
    om.subscribe(events.append)

    class W:
        pass

    w = W()
    w.om, w.broker, w.clock, w.events = om, broker, clock, events
    return w


def req(qty=150, price=2500000):
    return OrderRequest("", "acc-1", "ayush", FUT, Side.BUY, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=price)  # fmt: skip


async def working(world, qty=150):
    view = await world.om.submit(req(qty))
    world.om.apply(OrderUpdate(view.client_order_id, "F1", S.OPEN))
    return view.client_order_id


def terms(cid, qty, price, status=S.OPEN, filled=0, at=None):
    return OrderUpdate(cid, "F1", status, filled, 2500000 if filled else 0, qty=qty,
                       price_paise=price, ts=at or T0)  # fmt: skip


# --- one change at a time ---------------------------------------------------------------------


async def test_a_second_change_while_one_is_on_its_way_is_refused_and_nothing_reverts(world):
    cid = await working(world)
    world.broker.hold = asyncio.Event()
    first = asyncio.ensure_future(world.om.modify(cid, price_paise=2499000))
    await asyncio.sleep(0)
    assert world.om.get(cid).pending is Pending.MODIFY
    with pytest.raises(OrderError, match="a change to this order is already on its way"):
        await world.om.modify(cid, price_paise=2498000)
    world.broker.hold.set()
    done = await first
    assert (done.request.price_paise, done.pending) == (2499000, Pending.NONE)
    assert len(world.broker.modified) == 1  # the refused one never went out
    later = await world.om.modify(cid, price_paise=2498000)  # now it may
    assert later.request.price_paise == 2498000


async def test_a_refused_or_cancelled_change_frees_the_order_for_the_next(world):
    cid = await working(world)
    world.broker.refuse = "Maximum allowed order modifications exceeded"
    with pytest.raises(OrderError, match="Maximum allowed"):
        await world.om.modify(cid, price_paise=2499000)
    assert world.om.get(cid).pending is Pending.NONE
    world.broker.refuse = None

    class SlowGate:
        def __init__(self):
            self.release = asyncio.Event()

        async def admit_modify(self, order, new):
            await self.release.wait()

        async def admit_cancel(self, order):
            return None

    gate = SlowGate()
    world.om._gate = gate
    change = asyncio.ensure_future(world.om.modify(cid, price_paise=2499000))
    await asyncio.sleep(0)
    with pytest.raises(OrderError, match="already on its way"):  # even while it waits
        await world.om.modify(cid, price_paise=2497000)
    await world.om.cancel(cid)  # cancels always win
    gate.release.set()
    with pytest.raises(OrderError, match="finished or is being cancelled: nothing changed"):
        await change
    assert world.broker.modified == []


# --- a change with no answer --------------------------------------------------------------------


async def test_a_bigger_change_with_no_answer_counts_the_bigger_size_until_the_broker_says(world):
    cid = await working(world)
    world.broker.no_answer = True
    with pytest.raises(OrderError, match="may or may not have been made"):
        await world.om.modify(cid, qty=300, price_paise=2499000)
    doubt = world.om.get(cid)
    assert (doubt.pending, doubt.request.qty, doubt.request.price_paise) == (
        Pending.MODIFY, 300, 2499000)  # fmt: skip
    assert [o.request.qty for o in world.om.working()] == [300]  # risk sees the larger size
    assert world.events[-1].kind is EventKind.MODIFY_UNCONFIRMED
    assert "counting 300 until the broker's terms say" in world.events[-1].detail
    with pytest.raises(OrderError, match="already on its way"):
        await world.om.modify(cid, price_paise=2498000)
    # the broker's list says the change did NOT happen: back to its real terms
    world.om.reconcile([terms(cid, 150, 2500000)])
    real = world.om.get(cid)
    assert (real.pending, real.request.qty, real.request.price_paise) == (
        Pending.NONE, 150, 2500000)  # fmt: skip
    assert world.events[-1].detail == "the broker's terms: qty 150, price 2500000"


async def test_a_smaller_change_with_no_answer_keeps_the_bigger_size_until_it_is_confirmed(world):
    cid = await working(world)
    world.broker.no_answer = True
    with pytest.raises(OrderError):
        await world.om.modify(cid, qty=75)
    assert world.om.get(cid).request.qty == 150  # not counted smaller until the broker says
    world.om.reconcile([terms(cid, 75, 2500000)])  # it did happen
    assert (world.om.get(cid).request.qty, world.om.get(cid).pending) == (75, Pending.NONE)


async def test_the_follow_up_asks_the_broker_about_a_change(world):
    world.om._follow_up_s = (2, 5)
    cid = await working(world)
    world.broker.no_answer = True
    with pytest.raises(OrderError):
        await world.om.modify(cid, qty=300)
    world.broker.listing = [terms(cid, 300, 2500000)]
    for task in list(world.om._followers):
        await task
    assert (world.om.get(cid).request.qty, world.om.get(cid).pending) == (300, Pending.NONE)


async def test_a_streamed_update_older_than_a_confirmed_change_cant_undo_it(world):
    cid = await working(world)
    world.clock.advance(10)
    await world.om.modify(cid, price_paise=2499000)  # confirmed at T0+10s
    world.om.apply(terms(cid, 150, 2500000, at=T0 + timedelta(seconds=5)))  # older: ignored
    assert world.om.get(cid).request.price_paise == 2499000
    assert world.events[-1].kind is EventKind.IGNORED
    world.om.apply(terms(cid, 150, 2497000, at=T0 + timedelta(seconds=20)))  # newer: counts
    assert world.om.get(cid).request.price_paise == 2497000


async def test_kite_updates_carry_the_terms_and_a_market_price_is_none():
    row = {"order_id": "1", "status": "OPEN", "tag": "MT1", "quantity": 150, "price": 25000.5,
           "trigger_price": 0, "filled_quantity": 0, "average_price": 0,
           "order_timestamp": "2026-10-14 09:30:00"}  # fmt: skip
    u = to_update(row)
    assert (u.qty, u.price_paise, u.trigger_paise) == (150, 2500050, None)
    assert to_update({**row, "price": 0}).price_paise is None
    bare = {k: v for k, v in row.items() if k not in ("quantity", "price", "trigger_price")}
    assert (to_update(bare).qty, to_update(bare).price_paise) == (None, None)


# --- a cancel with no answer ---------------------------------------------------------------------


async def test_a_cancel_with_no_answer_stays_on_its_way_and_can_be_pressed_again(world):
    cid = await working(world)
    world.broker.no_answer = True
    with pytest.raises(OrderError, match="the cancel may have gone through"):
        await world.om.cancel(cid)
    doubt = world.om.get(cid)
    assert doubt.pending is Pending.CANCEL and doubt.status is S.OPEN  # not "working as usual"
    world.broker.no_answer = False
    await world.om.cancel(cid)  # pressing again sends it again
    assert world.broker.cancelled == ["F1"]
    world.om.apply(OrderUpdate(cid, "F1", S.CANCELLED, reason="cancelled"))
    assert world.om.get(cid).status is S.CANCELLED


async def test_the_brokers_list_settles_a_cancel_with_no_answer_either_way(world):
    first = await working(world)
    world.broker.no_answer = True
    with pytest.raises(OrderError):
        await world.om.cancel(first)
    world.om.reconcile([OrderUpdate(first, "F1", S.CANCELLED, reason="cancelled")])
    assert world.om.get(first).status is S.CANCELLED  # it had gone through

    second = (await world.om.submit(req())).client_order_id
    world.om.apply(OrderUpdate(second, "F2", S.OPEN))
    with pytest.raises(OrderError):
        await world.om.cancel(second)
    world.om.reconcile([OrderUpdate(second, "F2", S.OPEN)])  # still working: it didn't
    back = world.om.get(second)
    assert (back.status, back.pending) == (S.OPEN, Pending.NONE)
    assert world.events[-1].detail == (
        "the cancel didn't go through: the broker still has it working")  # fmt: skip


async def test_a_clear_refusal_of_a_cancel_changes_nothing(world):
    cid = await working(world)
    world.broker.refuse = "order is already filled"
    with pytest.raises(OrderError, match="already filled"):
        await world.om.cancel(cid)
    assert world.om.get(cid).pending is Pending.NONE


async def test_fills_survive_a_cancel_race(world):
    cid = await working(world)
    await world.om.cancel(cid)
    world.om.apply(OrderUpdate(cid, "F1", S.PARTIAL, 75, 2500000, 75, 2500000))  # filled first
    world.om.apply(OrderUpdate(cid, "F1", S.OPEN, 0, 0))  # stale: less filled, ignored
    world.om.apply(OrderUpdate(cid, "F1", S.CANCELLED, 75, 2500000, reason="cancelled"))
    done = world.om.get(cid)
    assert (done.status, done.filled_qty) == (S.CANCELLED, 75)
    assert sum(e.fill.qty for e in world.events if e.fill) == 75  # once


async def test_after_a_restart_a_change_or_cancel_on_its_way_is_in_doubt(world):
    cid = await working(world)
    saved = world.om.get(cid)
    again = OrderManager("acc-1", world.broker, world.om._instrument, ids=ClientIds("MT"),
                         clock=world.clock, follow_up_s=())  # fmt: skip
    again.restore(
        [
            replace(saved, pending=Pending.MODIFY),
        ]
    )
    assert OrderManager._in_doubt(again._orders[cid])
    again.reconcile([terms(cid, 150, 2500000)])
    assert again.get(cid).pending is Pending.NONE  # settled by the broker's list
    third = OrderManager("acc-1", world.broker, world.om._instrument, ids=ClientIds("MT"),
                         clock=world.clock, follow_up_s=())  # fmt: skip
    third.restore([replace(saved, pending=Pending.CANCEL)])
    assert third._orders[cid].cancel_unclear
    third.reconcile([OrderUpdate(cid, "F1", S.OPEN)])
    assert third.get(cid).pending is Pending.NONE  # still working: the cancel didn't happen


# --- risk checked again after the throttle made an order wait ------------------------------------


class Limits:
    recheck_after_wait = True

    def __init__(self):
        self.max_qty = 300
        self.asked = 0

    async def admit_new(self, request):
        self.asked += 1
        return None if request.qty <= self.max_qty else f"over the limit of {self.max_qty}"

    async def admit_modify(self, order, new):
        return await self.admit_new(new)


class Other:  # not marked: never asked twice
    def __init__(self):
        self.asked = 0

    async def admit_new(self, request):
        self.asked += 1


class Throttle:
    def __init__(self, clock, limits, wait_s):
        self.clock, self.limits, self.wait_s = clock, limits, wait_s

    async def admit_new(self, request):
        self.limits.max_qty = 100  # an admin tightens the limit while the order waits
        self.clock[0] += self.wait_s


async def test_a_limit_changed_while_the_throttle_held_the_order_is_respected():
    now = [0.0]
    limits, other = Limits(), Other()
    gates = Gates([other, limits, Throttle(now, limits, wait_s=1.0)], monotonic=lambda: now[0])
    reason = await gates.admit_new(req(qty=150))
    assert reason == "over the limit of 100 (checked again after waiting for the order rate)"
    assert (limits.asked, other.asked) == (2, 1)


async def test_no_wait_no_second_look():
    now = [0.0]
    limits = Limits()
    gates = Gates([limits, Throttle(now, limits, wait_s=0.0)], monotonic=lambda: now[0])
    assert await gates.admit_new(req(qty=150)) is None  # passed before the (instant) throttle
    assert limits.asked == 1


def test_the_real_risk_checks_are_looked_at_again_and_the_throttle_never_is():
    from laddrix.risk.checks import RiskGate
    from laddrix.risk.throttle import OrderThrottle

    assert RiskGate.recheck_after_wait is True
    assert getattr(OrderThrottle, "recheck_after_wait", False) is False  # it is what waits


async def test_the_brokers_list_is_the_order_as_it_is_now_whatever_its_timestamp(world):
    cid = await working(world)
    world.clock.advance(10)
    await world.om.modify(cid, price_paise=2499000)  # confirmed at T0+10s
    # the order book's row carries the order's last exchange time (T0+5s), yet it is fetched
    # now: it is the truth (a streamed update with that time would be ignored)
    world.om.reconcile([terms(cid, 150, 2498000, at=T0 + timedelta(seconds=5))])
    assert world.om.get(cid).request.price_paise == 2498000
