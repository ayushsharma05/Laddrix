"""Unanswered orders (#259): a place with no clear answer stays UNCONFIRMED, its risk still
counted, until the broker's own order list settles it. One list that misses it proves nothing
(a slow broker lists it late); it is rejected only when UNSEEN_CHECKS lists over at least
UNSEEN_AFTER all miss it, saying so. The manager asks again by itself, after a restart too;
an unknown broker status is never dropped; a person can settle what stays unclear."""

from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.kite.orders import to_update
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from mocktrade.orders.manager import UNSEEN_AFTER, UNSEEN_CHECKS, OrderManager
from mocktrade.orders.model import ClientIds, EventKind, OrderError, Pending
from mocktrade.refdata.store import InstrumentStore

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


class Timeouts(FakeBroker):
    """A broker whose place never answers, and whose order list we script."""

    def __init__(self):
        super().__init__()
        self.sent = 0
        self.lists = 0
        self.listing: list[OrderUpdate] = []
        self.failing_lists = 0

    async def place(self, request):
        self.sent += 1
        raise TimeoutError("read timed out")

    async def orders(self):
        self.lists += 1
        if self.failing_lists:
            self.failing_lists -= 1
            raise ConnectionError("the broker can't answer either")
        return list(self.listing)


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    clock, broker, events, sleeps = Clock(), Timeouts(), [], []

    async def sleep(seconds):  # time passes when the manager waits
        sleeps.append(seconds)
        clock.advance(seconds)

    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock,
                      follow_up_s=(), sleep=sleep)  # fmt: skip
    om.subscribe(events.append)

    class W:
        pass

    w = W()
    w.om, w.broker, w.clock, w.events, w.sleeps, w.store = om, broker, clock, events, sleeps, store
    return w


def req():
    return OrderRequest("", "acc-1", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT, Product.NRML,
                        price_paise=2500000)  # fmt: skip


def listed(view, status=S.OPEN, broker_id="K1"):
    return OrderUpdate(view.client_order_id, broker_id, status)


async def test_late_acceptance_after_an_empty_list_gives_one_order_never_two(world):
    view = await world.om.submit(req())
    assert (view.status, view.pending) == (S.PENDING, Pending.UNCONFIRMED)
    world.clock.advance(5)
    world.om.reconcile([])  # the broker hasn't listed it yet
    assert world.om.get(view.client_order_id).pending is Pending.UNCONFIRMED  # not rejected
    assert [o.client_order_id for o in world.om.working()] == [view.client_order_id]  # risk held
    world.clock.advance(5)
    world.om.reconcile([listed(view)])  # there it is
    found = world.om.get(view.client_order_id)
    assert (found.status, found.pending, found.broker_order_id) == (S.OPEN, Pending.NONE, "K1")
    assert world.broker.sent == 1  # never a blind retry
    assert len(world.om.orders()) == 1


async def test_rejected_only_after_enough_lists_over_enough_time_with_the_evidence(world):
    view = await world.om.submit(req())
    for _ in range(UNSEEN_CHECKS + 2):  # many lists, but all within a few seconds
        world.clock.advance(1)
        world.om.reconcile([])
    assert world.om.get(view.client_order_id).pending is Pending.UNCONFIRMED
    world.clock.advance(UNSEEN_AFTER.total_seconds())
    world.om.reconcile([])
    gone = world.om.get(view.client_order_id)
    assert gone.status is S.REJECTED and gone.pending is Pending.NONE
    assert gone.reason == (
        "the broker never received this order: not in its order list at 6 checks from "
        "04:00:01 to 04:01:05 UTC, 65 s after it was sent"
    )
    assert world.events[-1].kind is EventKind.REJECTED


async def test_one_late_list_after_a_long_wait_is_still_not_enough(world):
    view = await world.om.submit(req())
    world.clock.advance(600)  # ten minutes later, but only one look
    world.om.reconcile([])
    assert world.om.get(view.client_order_id).pending is Pending.UNCONFIRMED


async def test_the_manager_asks_again_by_itself_and_stops_once_settled(world):
    world.om._follow_up_s = (2, 5, 10, 20, 30, 60, 60)
    view = await world.om.submit(req())
    world.broker.failing_lists = 1  # the first follow-up can't get an answer either

    async def lists_it_on_the_third_look():
        if world.broker.lists == 2:
            world.broker.listing = [listed(view)]

    real = world.broker.orders

    async def orders():
        await lists_it_on_the_third_look()
        return await real()

    world.broker.orders = orders
    for task in list(world.om._followers):
        await task
    found = world.om.get(view.client_order_id)
    assert (found.status, found.broker_order_id) == (S.OPEN, "K1")
    assert world.sleeps == [2, 5, 10]  # stopped asking once it was settled


async def test_still_unclear_after_every_follow_up_it_asks_for_a_person(world):
    world.om._follow_up_s = (1, 1)  # too soon to reject: it stays unconfirmed
    view = await world.om.submit(req())
    for task in list(world.om._followers):
        await task
    still = world.om.get(view.client_order_id)
    assert still.pending is Pending.UNCONFIRMED and world.broker.lists == 2
    last = world.events[-1]
    assert last.kind is EventKind.UNCONFIRMED
    assert last.detail == ("the order is still not settled after asking the broker 2 times: "
                           "check the broker's own order book, then settle it here")  # fmt: skip


async def test_follow_ups_reject_when_the_evidence_is_there(world):
    world.om._follow_up_s = (2, 5, 10, 20, 30, 60, 60)
    view = await world.om.submit(req())
    for task in list(world.om._followers):
        await task
    gone = world.om.get(view.client_order_id)
    assert gone.status is S.REJECTED and "not in its order list at" in gone.reason
    assert sum(world.sleeps) >= UNSEEN_AFTER.total_seconds()


async def test_only_one_follow_up_per_order(world):
    world.om._follow_up_s = (1,)
    await world.om.submit(req())
    world.om.follow_unconfirmed()
    world.om.follow_unconfirmed()
    assert len(world.om._followers) == 1
    await world.om.aclose()
    assert not world.om._followers


async def test_after_a_restart_an_unanswered_order_is_followed_not_judged_on_one_list(world):
    view = await world.om.submit(req())
    saved = [world.om.get(view.client_order_id)]
    await world.om.aclose()

    clock = Clock()
    clock.now = world.clock.now + timedelta(minutes=10)
    again = OrderManager("acc-1", world.broker, world.store.get, ids=ClientIds("MT"),
                         clock=clock, follow_up_s=(), sleep=world.om._sleep)  # fmt: skip
    assert again.restore(saved) == 1
    again.reconcile([])  # recovery's one list
    assert again.get(view.client_order_id).pending is Pending.UNCONFIRMED
    again.follow_unconfirmed()
    assert len(again._followers) == 1
    await again.aclose()


async def test_a_person_settles_what_stays_unclear(world):
    view = await world.om.submit(req())
    settled = world.om.settle_unconfirmed(view.client_order_id, by="ayush")
    assert settled.status is S.REJECTED
    assert settled.reason == "settled by ayush: not in the broker's own order book, so never placed"
    with pytest.raises(OrderError, match="only an unconfirmed order the broker never named"):
        world.om.settle_unconfirmed(view.client_order_id, by="ayush")
    with pytest.raises(OrderError, match="no order MT9"):
        world.om.settle_unconfirmed("MT9", by="ayush")
    other = await world.om.submit(req())
    world.om.reconcile([listed(other, broker_id="K7")])  # the broker named it: settles itself
    with pytest.raises(OrderError, match="only an unconfirmed order"):
        world.om.settle_unconfirmed(other.client_order_id, by="ayush")
    await world.om.aclose()


# --- a status the broker uses that MockTrade doesn't know ------------------------------------


def kite_row(status, tag="MT1", order_id="250101"):
    return {"order_id": order_id, "status": status, "tag": tag, "filled_quantity": 0,
            "average_price": 0, "order_timestamp": "2026-10-14 09:30:00"}  # fmt: skip


def test_an_unknown_kite_status_is_kept_as_proof_the_order_exists():
    update = to_update(kite_row("SOMETHING NEW"))
    assert (update.client_order_id, update.broker_order_id, update.status) == (
        "MT1", "250101", S.PENDING)  # fmt: skip
    assert update.reason == "the broker reports a status MockTrade doesn't know: SOMETHING NEW"
    assert "(none)" in to_update(kite_row("")).reason


async def test_an_unknown_status_settles_an_unanswered_order_and_never_moves_one_back(world):
    view = await world.om.submit(req())
    world.om.reconcile([to_update(kite_row("SOMETHING NEW", tag=view.client_order_id))])
    now = world.om.get(view.client_order_id)
    assert (now.status, now.pending, now.broker_order_id) == (S.PENDING, Pending.NONE, "250101")
    world.om.apply(OrderUpdate(view.client_order_id, "250101", S.OPEN))
    world.om.apply(to_update(kite_row("SOMETHING NEW", tag=view.client_order_id)))
    assert world.om.get(view.client_order_id).status is S.OPEN  # never back
    ignored = world.events[-1]
    assert ignored.kind is EventKind.IGNORED
    assert "doesn't know: SOMETHING NEW" in ignored.detail  # visible in the audit trail
    await world.om.aclose()
