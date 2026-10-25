"""The event log: order, durability, restart, failures, and the stored format."""

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
    Validity,
)
from laddrix.orders.codec import view_from_dict, view_to_dict
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind, OrderView, Pending
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.kill_switch import KillSwitch
from laddrix.store.events import KILL_SWITCH, EventLog

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 14, 9, 15, tzinfo=UTC)


@pytest.fixture
def store(nifty_fut):
    s = InstrumentStore()
    s.add(nifty_fut)
    return s


@pytest.fixture
def path(tmp_path):
    return tmp_path / "events.db"


@pytest.fixture
async def log(path):
    event_log = EventLog(path)
    event_log.start()
    yield event_log
    event_log.close()


def clock_from(start=T0):
    state = {"t": start}

    def now():
        state["t"] += timedelta(milliseconds=1)
        return state["t"]

    return now


def req(cid="", **changes) -> OrderRequest:
    base = OrderRequest(
        client_order_id=cid,
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=FUT,
        side=Side.BUY,
        qty=150,
        order_type=OrderType.LIMIT,
        product=Product.NRML,
        price_paise=2501000,
    )
    return replace(base, **changes)


def manager(store, broker, log, prefix="MT261014", start=0, clock=None):
    om = OrderManager(
        "acc-1", broker, store.get, ids=ClientIds(prefix, start), clock=clock or clock_from()
    )
    om.subscribe(log.record)
    return om


# --- writing and reading ---


async def test_an_orders_whole_life_is_in_its_history(store, log):
    om = manager(store, FakeBroker(), log)
    view = await om.submit(req())
    om.apply(OrderUpdate(view.client_order_id, "F1", S.OPEN))
    om.apply(OrderUpdate(view.client_order_id, "F1", S.FILLED, 150, 2501000, 150, 2501000, ts=T0))
    await log.flush()
    history = log.history(view.client_order_id)
    assert [e.kind for e in history] == ["SUBMITTED", "SENT", "UPDATED", "UPDATED", "FILL"]
    assert [e.seq for e in history] == sorted(e.seq for e in history)
    assert history[-1].fill.qty == 150 and history[-1].fill.exact
    assert history[-1].order.status is S.FILLED
    assert history[0].ts < history[-1].ts


async def test_events_come_back_in_the_order_written(log):
    om_events = []
    for i in range(2000):
        cid = f"X{i % 7}"
        om_events.append(cid)
        log.record(_event(cid, EventKind.SUBMITTED, T0 + timedelta(microseconds=i)))
    await log.flush()
    stored = log.events(limit=5000)
    assert [e.client_order_id for e in stored] == om_events
    assert log.written == 2000


async def test_events_can_be_read_in_pages_and_by_account(log):
    for i in range(5):
        log.record(_event(f"A{i}", EventKind.SUBMITTED, T0, account_id="acc-1"))
        log.record(_event(f"B{i}", EventKind.SUBMITTED, T0, account_id="acc-2"))
    await log.flush()
    first = log.events(limit=3)
    rest = log.events(after_seq=first[-1].seq, limit=100)
    assert len(first) + len(rest) == 10
    assert [e.client_order_id for e in log.events(account_id="acc-2")] == [
        f"B{i}" for i in range(5)
    ]


async def test_audit_pages_are_newest_first_and_cover_the_kill_switches(log):
    for i in range(6):
        log.record(_event(f"A{i}", EventKind.SUBMITTED, T0, account_id="acc-1"))
        log.record(_event(f"B{i}", EventKind.SUBMITTED, T0, account_id="acc-2"))
    switch = KillSwitch("account acc-1", clock=clock_from())
    switch.on_change(log.record_kill)
    switch.engage("break", by="ayush")
    await log.flush()
    names = ["acc-1", "account acc-1", "firm"]

    newest = log.page(names, limit=3)
    assert [e.client_order_id or e.kind for e in newest] == [KILL_SWITCH, "A5", "A4"]
    older = log.page(names, before_seq=newest[-1].seq, limit=10)
    assert [e.client_order_id for e in older] == ["A3", "A2", "A1", "A0"]
    assert log.page(names, before_seq=older[-1].seq) == []

    log.record(_event("A6", EventKind.SUBMITTED, T0, account_id="acc-1"))
    log.record(_event("A7", EventKind.SUBMITTED, T0, account_id="acc-1"))
    await log.flush()
    live = log.page(names, after_seq=newest[0].seq)
    assert [e.client_order_id for e in live] == ["A7", "A6"]
    # a capped catch-up takes the oldest missing events first, so paging on never skips any
    assert [e.client_order_id for e in log.page(names, after_seq=newest[0].seq, limit=1)] == ["A6"]
    assert log.page([], limit=5) == []


async def test_everything_survives_closing_and_reopening(store, path):
    first = EventLog(path)
    first.start()
    om = manager(store, FakeBroker(), first)
    view = await om.submit(req())
    first.close()  # writes everything still queued
    again = EventLog(path)
    assert [e.kind for e in again.history(view.client_order_id)] == ["SUBMITTED", "SENT"]


async def test_close_writes_events_still_in_the_queue(path):
    event_log = EventLog(path)
    event_log.start()
    for i in range(1200):  # more than two batches of 500
        event_log.record(_event(f"Q{i}", EventKind.SUBMITTED, T0))
    event_log.close()
    assert len(EventLog(path).events(limit=5000)) == 1200
    with pytest.raises(RuntimeError, match="closed"):
        event_log.record(_event("late", EventKind.SUBMITTED, T0))
    event_log.close()  # safe twice


async def test_flush_needs_a_started_log(path):
    with pytest.raises(RuntimeError, match="not started"):
        await EventLog(path).flush()


async def test_kill_switch_changes_are_audited(log):
    switch = KillSwitch("account acc-1", clock=clock_from())
    switch.on_change(log.record_kill)
    switch.engage("user pressed STOP", by="u-ayush")
    switch.reset(by="u-admin")
    await log.flush()
    stored = [e for e in log.events() if e.kind == KILL_SWITCH]
    assert [(e.account_id, e.detail, e.extra) for e in stored] == [
        ("account acc-1", "user pressed STOP", {"engaged": True, "by": "u-ayush"}),
        ("account acc-1", "reset", {"engaged": False, "by": "u-admin"}),
    ]


# --- restart ---


async def test_restart_rebuilds_orders_reconciles_and_continues_ids(store, path):
    first_log = EventLog(path)
    first_log.start()
    broker = FakeBroker()
    om = manager(store, broker, first_log)
    filled = await om.submit(req())
    working = await om.submit(req(price_paise=2500990))
    om.apply(OrderUpdate(filled.client_order_id, "F1", S.FILLED, 150, 2501000))
    om.apply(OrderUpdate(working.client_order_id, "F2", S.OPEN))
    await first_log.flush()
    first_log.close()  # ...the app stops; while it's down, F2 part-fills

    log = EventLog(path)
    log.start()
    views = log.latest_views("acc-1")
    assert [(v.client_order_id, v.status) for v in views] == [
        ("MT2610141", S.FILLED),
        ("MT2610142", S.OPEN),
    ]
    restarted = manager(store, broker, log, start=log.last_counter("MT261014"))
    assert restarted.restore(views) == 2
    assert restarted.restore(views) == 0  # loading twice changes nothing
    restarted.reconcile([OrderUpdate("MT2610142", "F2", S.OPEN, 75, 2500990)])
    assert restarted.get("MT2610142").status is S.PARTIAL
    assert restarted.by_broker_id("F2").client_order_id == "MT2610142"
    fresh = await restarted.submit(req())
    assert fresh.client_order_id == "MT2610143"  # never reuses an id
    log.close()


async def test_an_order_caught_mid_send_comes_back_unconfirmed(store, log):
    view = OrderView(
        request=req("MT2610147"),
        broker_order_id=None,
        status=S.PENDING,
        filled_qty=0,
        avg_price_paise=0,
        reason="",
        pending=Pending.NONE,
        created_at=T0,
        updated_at=T0,
    )
    om = manager(store, FakeBroker(), log)
    om.restore([view])
    assert om.get("MT2610147").pending is Pending.UNCONFIRMED
    om.reconcile([])  # one list without it proves nothing: still followed up (#259)
    assert om.get("MT2610147").status is S.PENDING
    assert om.get("MT2610147").pending is Pending.UNCONFIRMED


async def test_restore_skips_other_accounts(store, log):
    om = manager(store, FakeBroker(), log)
    other = replace(_view("Z1"), request=req("Z1", account_id="acc-2"))
    assert om.restore([other]) == 0


async def test_latest_views_can_be_limited_to_today(log):
    yesterday = _view("OLD1", created=T0 - timedelta(days=1))
    log.record(_event("OLD1", EventKind.UPDATED, T0, order=yesterday))
    log.record(_event("NEW1", EventKind.UPDATED, T0, order=_view("NEW1")))
    log.record(_event("", EventKind.EXTERNAL, T0))
    await log.flush()
    assert [v.client_order_id for v in log.latest_views("acc-1")] == ["OLD1", "NEW1"]
    assert [v.client_order_id for v in log.latest_views("acc-1", since=T0)] == ["NEW1"]


async def test_last_counter_reads_only_ids_with_that_prefix(log):
    for cid in ["MT2610141", "MT26101412", "MT2610133", "MT261014X", "ZZ9"]:
        log.record(_event(cid, EventKind.SUBMITTED, T0))
    await log.flush()
    assert log.last_counter("MT261014") == 12
    assert log.last_counter("MT261015") == 0
    with pytest.raises(ValueError):
        log.last_counter("MT%")


# --- when writing fails ---


async def test_a_failed_write_is_retried_and_nothing_is_lost(path):
    alerts = []
    event_log = EventLog(path, on_failure=alerts.append, max_backoff_s=0.05)
    original = event_log._write
    failures = {"left": 2}

    def flaky(db, rows):
        if failures["left"]:
            failures["left"] -= 1
            raise sqlite3.OperationalError("database is locked")
        original(db, rows)

    event_log._write = flaky
    event_log.start()
    for i in range(10):
        event_log.record(_event(f"R{i}", EventKind.SUBMITTED, T0))
    await asyncio.wait_for(event_log.flush(), 5)
    await asyncio.sleep(0)  # let the alert scheduled on the loop run
    assert [e.client_order_id for e in event_log.events()] == [f"R{i}" for i in range(10)]
    assert len(alerts) == 1 and "locked" in str(alerts[0])  # once per outage
    assert event_log.healthy
    event_log.close()


async def test_the_trading_loop_never_waits_for_the_disk(path):
    event_log = EventLog(path)
    gate = asyncio.Event()
    original = event_log._write

    def slow(db, rows):
        while not gate.is_set():
            pass  # a stuck disk
        original(db, rows)

    event_log._write = slow
    event_log.start()
    loop = asyncio.get_running_loop()
    started = loop.time()
    for i in range(1000):
        event_log.record(_event(f"S{i}", EventKind.SUBMITTED, T0))
    assert loop.time() - started < 0.5  # recording returned at once
    gate.set()
    await event_log.flush()
    assert event_log.written == 1000
    event_log.close()


# --- the stored format ---


def test_order_views_round_trip_through_the_stored_format():
    view = OrderView(
        request=req(
            "MT1",
            order_type=OrderType.SL_M,
            price_paise=None,
            trigger_paise=2500500,
            market_protection=Decimal("1.5"),
            validity=Validity.IOC,
            algo_id="ALG7",
            parent_id="P9",
            side=Side.SELL,
        ),
        broker_order_id="K123",
        status=S.PARTIAL,
        filled_qty=75,
        avg_price_paise=2500400,
        reason="",
        pending=Pending.CANCEL,
        created_at=T0,
        updated_at=T0 + timedelta(seconds=3),
    )
    assert view_from_dict(view_to_dict(view)) == view


def test_old_records_without_newer_fields_still_load():
    stored = view_to_dict(_view("MT1"))
    for optional in ("pending", "reason"):
        del stored[optional]
    del stored["request"]["validity"]
    view = view_from_dict(stored)
    assert (view.pending, view.reason, view.request.validity) == (Pending.NONE, "", Validity.DAY)


# --- helpers ---


def _view(cid, created=T0) -> OrderView:
    return OrderView(
        request=req(cid),
        broker_order_id="B" + cid,
        status=S.OPEN,
        filled_qty=0,
        avg_price_paise=0,
        reason="",
        pending=Pending.NONE,
        created_at=created,
        updated_at=created,
    )


def _event(cid, kind, ts, account_id="acc-1", order=None):
    from laddrix.orders.model import OrderEvent

    return OrderEvent(kind, ts, account_id, cid, order, detail="")
