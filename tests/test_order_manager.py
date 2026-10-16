"""The order manager: life of an order, odd broker behaviour, and recovery."""

import asyncio
import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.broker.base import BrokerError
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders import manager as manager_module
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds, EventKind, OrderError, Pending
from mocktrade.refdata.store import InstrumentStore

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 9, 15, tzinfo=UTC)


@pytest.fixture
def store(nifty_fut, reliance):
    s = InstrumentStore()
    s.add(nifty_fut)
    s.add(reliance)
    return s


@pytest.fixture
def clock():
    ticks = itertools.count()
    return lambda: T0 + timedelta(seconds=next(ticks))


@pytest.fixture
def broker():
    return FakeBroker()


@pytest.fixture
def events():
    return []


@pytest.fixture
def om(broker, store, clock, events):
    m = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    m.subscribe(events.append)
    return m


def req(cid="", qty=150, price=2501000, **changes) -> OrderRequest:
    base = OrderRequest(
        client_order_id=cid,
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=FUT,
        side=Side.BUY,
        qty=qty,
        order_type=OrderType.LIMIT,
        product=Product.NRML,
        price_paise=price,
    )
    return replace(base, **changes)


def upd(view_or_cid, status, filled=0, avg=0, broker_id="F1", **extra) -> OrderUpdate:
    cid = view_or_cid if isinstance(view_or_cid, str) else view_or_cid.client_order_id
    return OrderUpdate(cid, broker_id, status, filled, avg, **extra)


def kinds(events):
    return [e.kind for e in events]


class Gate:
    """A test gate: refuses with a reason, or waits until released."""

    def __init__(self, reason=None):
        self.reason = reason
        self.release = asyncio.Event()
        self.release.set()
        self.seen = []

    async def admit_new(self, request):
        self.seen.append(("new", request.client_order_id))
        await self.release.wait()
        return self.reason

    async def admit_modify(self, order, new):
        self.seen.append(("modify", new.price_paise))
        return self.reason

    async def admit_cancel(self, order):
        self.seen.append(("cancel", order.client_order_id))
        return None


# --- ids ----------------------------------------------------------------------------------


def test_client_ids_count_up_from_where_they_left_off():
    ids = ClientIds("MT261014", start=41)
    assert (ids.next(), ids.next(), ids.last) == ("MT26101442", "MT26101443", 43)


@pytest.mark.parametrize("prefix", ["", "MT-1", "A" * 13])
def test_client_id_prefix_must_fit_a_broker_tag(prefix):
    with pytest.raises(ValueError):
        ClientIds(prefix)


def test_client_ids_never_exceed_twenty_characters():
    ids = ClientIds("A" * 12, start=99_999_998)
    assert len(ids.next()) == 20
    with pytest.raises(RuntimeError):
        ids.next()


# --- submit ---------------------------------------------------------------------------------


async def test_submit_sends_to_the_broker_and_maps_both_ids(om, broker, events):
    view = await om.submit(req())
    assert view.client_order_id == "MT1"  # empty id -> a fresh one
    assert (view.status, view.broker_order_id, view.pending) == (S.PENDING, "F1", Pending.NONE)
    assert broker.placed[0].client_order_id == "MT1"
    assert om.by_broker_id("F1") == om.get("MT1") == view
    assert kinds(events) == [EventKind.SUBMITTED, EventKind.SENT]
    assert events[0].account_id == "acc-1"


async def test_resubmitting_the_same_id_never_places_twice(om, broker):
    first = await om.submit(req("ABC1"))
    again = await om.submit(req("ABC1", qty=75))
    assert again == first and len(broker.placed) == 1


@pytest.mark.parametrize(
    "request_, reason",
    [
        (req(qty=50), "lot"),
        (req(price=2501005), "tick"),
        (req(instrument_id="NSE:NOPE"), "unknown instrument"),
        (req(account_id="acc-2"), "account acc-2"),
    ],
)
async def test_rule_breaks_are_rejected_before_the_broker(om, broker, events, request_, reason):
    view = await om.submit(request_)
    assert view.status is S.REJECTED and reason in view.reason
    assert broker.placed == []
    assert kinds(events) == [EventKind.SUBMITTED, EventKind.REJECTED]


async def test_gate_refusal_is_a_rejection_with_its_reason(broker, store, clock):
    gate = Gate("max position 150 for NIFTY")
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=gate, clock=clock)
    view = await om.submit(req())
    assert (view.status, view.reason) == (S.REJECTED, "max position 150 for NIFTY")
    assert broker.placed == []


async def test_broker_refusal_is_a_rejection(om, broker):
    broker.fail_next("Insufficient funds")
    view = await om.submit(req())
    assert (view.status, view.reason, view.broker_order_id) == (
        S.REJECTED,
        "Insufficient funds",
        None,
    )


async def test_no_clear_answer_is_never_assumed_rejected(om, broker, events):
    async def timeout(_):
        raise TimeoutError("read timed out")

    broker.place = timeout
    view = await om.submit(req())
    assert (view.status, view.pending) == (S.PENDING, Pending.UNCONFIRMED)
    assert events[-1].kind is EventKind.UNCONFIRMED


async def test_reconcile_finds_an_unconfirmed_order_the_broker_did_receive(om, broker):
    broker.place = _raises(ConnectionError("reset"))
    view = await om.submit(req())
    om.reconcile([upd(view, S.OPEN, broker_id="K9")])
    found = om.get(view.client_order_id)
    assert (found.status, found.pending, found.broker_order_id) == (S.OPEN, Pending.NONE, "K9")
    assert om.by_broker_id("K9") == found


async def test_one_list_without_an_unconfirmed_order_proves_nothing(om, broker):
    # a slow broker lists an order late: one list that misses it never rejects it (#259;
    # the full rule is in test_unconfirmed.py)
    broker.place = _raises(ConnectionError("reset"))
    view = await om.submit(req())
    assert om.reconcile([]) == []
    still = om.get(view.client_order_id)
    assert (still.status, still.pending) == (S.PENDING, Pending.UNCONFIRMED)
    await om.aclose()


def _raises(exc):
    async def fail(*args, **kwargs):
        raise exc

    return fail


# --- updates -------------------------------------------------------------------------------


async def test_full_life_with_fills(om, events):
    view = await om.submit(req())
    assert om.apply(upd(view, S.OPEN))
    assert om.apply(
        upd(view, S.PARTIAL, 75, 2501000, last_fill_qty=75, last_fill_price_paise=2501000)
    )
    assert om.apply(
        upd(view, S.FILLED, 150, 2500995, last_fill_qty=75, last_fill_price_paise=2500990)
    )
    done = om.get(view.client_order_id)
    assert (done.status, done.filled_qty, done.avg_price_paise, done.leaves_qty) == (
        S.FILLED,
        150,
        2500995,
        0,
    )
    fills = [(e.fill.qty, e.fill.price_paise, e.fill.exact) for e in events if e.fill]
    assert fills == [(75, 2501000, True), (75, 2500990, True)]
    assert om.working() == []


async def test_kite_style_open_with_some_filled_is_partial(om):
    view = await om.submit(req())
    om.apply(upd(view, S.OPEN, filled=75, avg=2501000))
    assert om.get(view.client_order_id).status is S.PARTIAL


async def test_fill_price_is_worked_out_from_averages_when_not_given(om, events):
    view = await om.submit(req())
    om.apply(upd(view, S.OPEN, filled=75, avg=2501000))
    om.apply(upd(view, S.FILLED, filled=150, avg=2501005))
    fills = [(e.fill.qty, e.fill.price_paise, e.fill.exact) for e in events if e.fill]
    assert fills == [(75, 2501000, False), (75, 2501010, False)]


@pytest.mark.parametrize(
    "sequence, final_status",
    [
        ([(S.OPEN, 0), (S.OPEN, 0)], S.OPEN),  # duplicate
        ([(S.FILLED, 150), (S.OPEN, 0)], S.FILLED),  # late OPEN after FILLED
        ([(S.PARTIAL, 75), (S.OPEN, 0)], S.PARTIAL),  # can't go back
        ([(S.PARTIAL, 75), (S.PARTIAL, 0)], S.PARTIAL),  # stale: less filled
        ([(S.CANCELLED, 0), (S.FILLED, 150)], S.CANCELLED),  # final is final
        ([(S.REJECTED, 0), (S.OPEN, 0)], S.REJECTED),
    ],
)
async def test_updates_only_move_forward(om, events, sequence, final_status):
    view = await om.submit(req())
    first, second = sequence
    assert om.apply(upd(view, first[0], first[1], 2501000))
    assert not om.apply(upd(view, second[0], second[1], 2501000))
    assert om.get(view.client_order_id).status is final_status
    assert events[-1].kind is EventKind.IGNORED


async def test_more_fills_on_a_partial_are_applied(om):
    view = await om.submit(req(qty=225))
    om.apply(upd(view, S.PARTIAL, 75, 2501000))
    assert om.apply(upd(view, S.PARTIAL, 150, 2501000))
    assert om.get(view.client_order_id).filled_qty == 150


async def test_update_arriving_before_place_returns_is_matched_by_client_id(store, clock):
    broker = FakeBroker()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    original = broker.place

    async def fast_exchange(request):
        om.apply(upd(request.client_order_id, S.OPEN, broker_id="F1"))  # beats the reply
        return await original(request)

    broker.place = fast_exchange
    view = await om.submit(req())
    assert (view.status, view.broker_order_id) == (S.OPEN, "F1")


async def test_update_with_only_a_broker_id_waits_until_that_id_is_known(store, clock):
    broker = FakeBroker()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    original = broker.place

    async def no_tag(request):
        assert not om.apply(upd("", S.OPEN, broker_id="F1"))  # can't match yet: parked
        return await original(request)

    broker.place = no_tag
    view = await om.submit(req())
    assert view.status is S.OPEN  # replayed once the broker id arrived


def test_unmatched_updates_are_bounded(om, monkeypatch):
    monkeypatch.setattr(manager_module, "MAX_PARKED", 2)
    for broker_id in ("X1", "X2", "X3"):
        om.apply(upd("", S.OPEN, broker_id=broker_id))
    assert list(om._parked) == ["X2", "X3"]


async def test_run_applies_the_broker_stream(om, broker):
    view = await om.submit(req())
    runner = asyncio.create_task(om.run())
    broker.push(upd(view, S.OPEN))
    broker.push(upd(view, S.FILLED, 150, 2501000))
    for _ in range(10):
        await asyncio.sleep(0)
    assert om.get(view.client_order_id).status is S.FILLED
    runner.cancel()


# --- modify ----------------------------------------------------------------------------------


async def test_modify_sends_the_change_and_updates_our_view(om, broker, events):
    view = await om.submit(req())
    om.apply(upd(view, S.OPEN))
    changed = await om.modify(view.client_order_id, price_paise=2500990)
    assert changed.request.price_paise == 2500990
    assert broker.modified == [("F1", {"qty": None, "price_paise": 2500990, "trigger_paise": None})]
    assert events[-1].kind is EventKind.MODIFY_SENT and "2500990" in events[-1].detail


@pytest.mark.parametrize(
    "changes, message",
    [
        ({}, "nothing to change"),
        ({"price_paise": 2501005}, "tick"),
        ({"qty": 75}, "above the 75 filled"),
        ({"qty": 100}, "lot"),
    ],
)
async def test_bad_modifications_are_refused_and_change_nothing(om, broker, changes, message):
    view = await om.submit(req())
    om.apply(upd(view, S.PARTIAL, 75, 2501000))
    with pytest.raises(OrderError, match=message):
        await om.modify(view.client_order_id, **changes)
    assert broker.modified == []
    assert om.get(view.client_order_id).request == view.request


async def test_modify_refused_by_the_broker_keeps_the_old_values(om, broker):
    view = await om.submit(req())
    broker.fail_next("Maximum allowed order modifications exceeded")
    with pytest.raises(OrderError, match="modifications exceeded"):
        await om.modify(view.client_order_id, price_paise=2500990)
    assert om.get(view.client_order_id).request.price_paise == 2501000


async def test_modify_without_a_clear_answer_says_so(om, broker):
    view = await om.submit(req())
    broker.modify = _raises(TimeoutError())
    with pytest.raises(OrderError, match="no answer"):
        await om.modify(view.client_order_id, price_paise=2500990)


async def test_modify_needs_a_working_acknowledged_order(om, broker):
    with pytest.raises(OrderError, match="unknown"):
        await om.modify("NOPE", price_paise=1)
    view = await om.submit(req())
    om.apply(upd(view, S.FILLED, 150, 2501000))
    with pytest.raises(OrderError, match="already filled"):
        await om.modify(view.client_order_id, price_paise=2500990)
    broker.place = _raises(TimeoutError())
    unconfirmed = await om.submit(req())
    with pytest.raises(OrderError, match="hasn't acknowledged"):
        await om.modify(unconfirmed.client_order_id, price_paise=2500990)


async def test_gate_sees_and_can_refuse_modifications(broker, store, clock):
    gate = Gate()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=gate, clock=clock)
    view = await om.submit(req())
    gate.reason = "price more than 5% from last price"
    with pytest.raises(OrderError, match="5%"):
        await om.modify(view.client_order_id, price_paise=2000000)
    assert ("modify", 2000000) in gate.seen and broker.modified == []


# --- cancel ------------------------------------------------------------------------------------


async def test_cancel_goes_to_the_broker_and_completes_on_its_update(om, broker, events):
    view = await om.submit(req())
    om.apply(upd(view, S.OPEN))
    asked = await om.cancel(view.client_order_id)
    assert (asked.pending, broker.cancelled) == (Pending.CANCEL, ["F1"])
    assert events[-1].kind is EventKind.CANCEL_SENT
    om.apply(upd(view, S.CANCELLED, reason="cancelled by user"))
    done = om.get(view.client_order_id)
    assert (done.status, done.pending, done.reason) == (
        S.CANCELLED,
        Pending.NONE,
        "cancelled by user",
    )


async def test_a_second_cancel_click_sends_nothing_more(om, broker):
    view = await om.submit(req())
    await om.cancel(view.client_order_id)
    await om.cancel(view.client_order_id)
    assert broker.cancelled == ["F1"]


async def test_modify_is_refused_while_a_cancel_is_on_its_way(om):
    view = await om.submit(req())
    await om.cancel(view.client_order_id)
    with pytest.raises(OrderError, match="cancel is already"):
        await om.modify(view.client_order_id, price_paise=2500990)


async def test_cancel_refused_by_the_broker_can_be_retried(om, broker):
    view = await om.submit(req())
    broker.fail_next("Order already completed")
    with pytest.raises(OrderError, match="already completed"):
        await om.cancel(view.client_order_id)
    assert om.get(view.client_order_id).pending is Pending.NONE
    await om.cancel(view.client_order_id)
    assert broker.cancelled == ["F1"]


async def test_cancel_while_waiting_at_the_gate_never_sends_the_order(broker, store, clock, events):
    gate = Gate()
    gate.release.clear()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=gate, clock=clock)
    om.subscribe(events.append)
    submitting = asyncio.create_task(om.submit(req()))
    await asyncio.sleep(0)
    await om.cancel("MT1")
    gate.release.set()
    view = await submitting
    assert (view.status, view.reason) == (S.CANCELLED, "cancelled before it was sent")
    assert broker.placed == [] and broker.cancelled == []
    assert events[-1].kind is EventKind.CANCELLED_LOCALLY


async def test_cancel_before_the_broker_id_is_known_is_sent_once_it_is(store, clock):
    broker = FakeBroker()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), clock=clock)
    reply = asyncio.Event()
    original = broker.place

    async def slow_reply(request):
        await reply.wait()
        return await original(request)

    broker.place = slow_reply
    submitting = asyncio.create_task(om.submit(req()))
    await asyncio.sleep(0)
    asked = await om.cancel("MT1")
    assert (asked.pending, broker.cancelled) == (Pending.CANCEL, [])
    om.apply(upd("MT1", S.OPEN, broker_id="F1"))  # the id arrives first in an update...
    reply.set()
    await submitting  # ...and again when place returns
    await om.settle()
    assert broker.cancelled == ["F1"]  # exactly once


async def test_cancel_all_reports_what_failed(om, broker):
    first = await om.submit(req())
    second = await om.submit(req())
    finished = await om.submit(req())
    om.apply(upd(finished, S.FILLED, 150, 2501000, broker_id="F3"))
    original = broker.cancel

    async def refuse_second(broker_order_id):
        if broker_order_id == "F2":
            raise BrokerError("exchange closed")
        await original(broker_order_id)

    broker.cancel = refuse_second
    failures = await om.cancel_all()
    assert failures == [(second.client_order_id, "exchange closed")]
    assert broker.cancelled == [first.broker_order_id]


# --- listeners and external orders ----------------------------------------------------------------


async def test_a_failing_listener_never_stops_order_handling(om, events):
    def broken(event):
        raise RuntimeError("screen crashed")

    om.subscribe(broken)
    view = await om.submit(req())
    assert view.status is S.PENDING and len(events) == 2


async def test_unsubscribed_listeners_hear_nothing_more(om):
    heard = []
    stop = om.subscribe(heard.append)
    await om.submit(req())
    stop()
    stop()  # safe to call twice
    await om.submit(req())
    assert len(heard) == 2


async def test_orders_placed_outside_the_app_are_reported_once_per_status(om, events):
    outside = OrderUpdate("", "K77", S.OPEN)
    assert om.reconcile([outside]) == [outside]
    om.reconcile([outside])
    om.reconcile([replace(outside, status=S.FILLED, filled_qty=1)])
    external = [e for e in events if e.kind is EventKind.EXTERNAL]
    assert [e.detail for e in external] == ["broker order K77: OPEN", "broker order K77: FILLED"]


async def test_timestamps_come_from_the_clock(om):
    view = await om.submit(req())
    assert view.created_at == T0 and view.updated_at > view.created_at


# --- with the paper broker ----------------------------------------------------------------------


async def test_end_to_end_with_the_paper_broker(store, clock, events):
    md = MarketData()
    md.on_tick(
        Tick(
            FUT,
            2501000,
            volume=1000,
            bids=(DepthLevel(2501000, 150, 1),),
            asks=(DepthLevel(2501010, 300, 2),),
        )
    )
    paper = PaperBroker(md, store, latency_s=0)
    om = OrderManager("acc-1", paper, store.get, ids=ClientIds("MT"), clock=clock)
    om.subscribe(events.append)
    runner = asyncio.create_task(om.run())
    view = await om.submit(req(price=2501010))
    await paper.settle()
    for _ in range(10):
        await asyncio.sleep(0)
    done = om.get(view.client_order_id)
    assert (done.status, done.filled_qty, done.avg_price_paise) == (S.FILLED, 150, 2501010)
    assert [(e.fill.qty, e.fill.exact) for e in events if e.fill] == [(150, True)]
    runner.cancel()
    await paper.aclose()


def test_transition_table_only_moves_forward():
    from mocktrade.orders.manager import ALLOWED

    rank = {S.PENDING: 0, S.OPEN: 1, S.PARTIAL: 2, S.FILLED: 3, S.CANCELLED: 3, S.REJECTED: 3}
    assert set(ALLOWED) == set(S)
    for start, targets in ALLOWED.items():
        assert all(rank[end] > rank[start] for end in targets), start
        if start.is_final:
            assert not targets
