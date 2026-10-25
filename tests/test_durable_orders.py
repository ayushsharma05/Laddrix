"""Orders on disk before the broker (#265).

The failure before: the audit log wrote in the background, after the order was already at the
broker. A crash in that moment left an order at the exchange that Laddrix had never heard
of after a restart, and its id could be given to a new order. A broken disk only logged an
error, and a stalled disk let memory grow without end. Now an order's record (and its id) is
on disk before the broker sees it; if the disk can't confirm, the order isn't sent, the
screens say why, and cancels and forced closes still go."""

import asyncio
from decimal import Decimal

import pytest

from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from laddrix.ops.alerts import Alerts
from laddrix.ops.checks import audit_log
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind, OrderEvent, Pending
from laddrix.refdata.store import InstrumentStore
from laddrix.store.events import EventLog, StorageError

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"


def req(side=Side.BUY, qty=75, *, forced_close=False):
    kind = OrderType.MARKET if forced_close else OrderType.LIMIT
    return OrderRequest("", "acc", "ayush", FUT, side, qty, kind, Product.NRML,
                        price_paise=None if forced_close else 2500000,
                        market_protection=Decimal(-1) if forced_close else None,
                        reduce_only=forced_close, forced_close=forced_close)  # fmt: skip


class Watching(FakeBroker):
    """A broker that, the moment an order reaches it, reads what is on disk."""

    def __init__(self, path):
        super().__init__()
        self.path, self.on_disk = path, []

    async def place(self, request):
        reader = EventLog(self.path)  # a separate reader: only what is committed to the file
        self.on_disk.append([e.kind for e in reader.history(request.client_order_id)])
        return await super().place(request)

    async def modify(self, broker_order_id, **changes):
        reader = EventLog(self.path)
        (view,) = reader.latest_views("acc")
        self.on_disk.append((view.pending, view.request.qty))
        return await super().modify(broker_order_id, **changes)


@pytest.fixture
async def world(nifty_fut, tmp_path):
    store = InstrumentStore()
    store.add(nifty_fut)
    log = EventLog(tmp_path / "events.db")
    log.start()
    broker = Watching(tmp_path / "events.db")
    om = OrderManager("acc", broker, store.get, ids=ClientIds("MT"), durable=log.persist,
                      follow_up_s=())  # fmt: skip
    om.subscribe(log.record)

    class W:
        pass

    w = W()
    w.log, w.broker, w.om, w.store, w.path = log, broker, om, store, tmp_path / "events.db"
    yield w
    await om.aclose()
    await log.aclose()


async def test_the_order_is_on_disk_before_the_broker_sees_it(world):
    view = await world.om.submit(req())
    assert view.status is not S.REJECTED, view.reason
    assert world.broker.on_disk == [[EventKind.SUBMITTED.value]]


async def test_a_crash_right_after_sending_keeps_its_identity_and_doubt(world, nifty_fut):
    """The broker took it; then the server died before anything else was written."""
    view = await world.om.submit(req())
    after_crash = EventLog(world.path)  # what a restarted server reads
    saved = after_crash.latest_views("acc")
    assert [v.client_order_id for v in saved] == [view.client_order_id]
    again = OrderManager("acc", FakeBroker(), world.store.get,
                         ids=ClientIds("MT", after_crash.last_counter("MT")),
                         follow_up_s=())  # fmt: skip
    again.restore(saved)
    known = again.get(view.client_order_id)
    assert known is not None  # never an unknown order at the exchange
    nxt = await again.submit(req())
    assert nxt.client_order_id != view.client_order_id  # its id is never given out again
    await again.aclose()


async def test_an_order_whose_record_did_not_reach_the_disk_is_not_sent(world):
    world.log.healthy = False
    world.log.last_error = OSError(28, "No space left on device")
    refused = await world.om.submit(req())
    assert refused.status is S.REJECTED and world.broker.placed == []
    assert refused.reason.startswith("not sent: it couldn't be recorded on disk first (the audit "
                                     "log can't write to events.db (OSError(28, ")  # fmt: skip
    assert refused.reason.endswith("new orders are refused until the audit log writes again")


async def test_a_forced_close_still_goes_and_cancels_are_never_blocked(world):
    working = await world.om.submit(req())
    world.log.healthy = False
    world.log.last_error = OSError("disk gone")
    close = await world.om.submit(req(Side.SELL, forced_close=True))
    assert close.status is not S.REJECTED  # an emergency close only reduces: it goes
    assert len(world.broker.placed) == 2
    await world.om.cancel(working.client_order_id)
    assert world.broker.cancelled == [working.broker_order_id]


async def test_a_change_is_on_disk_with_the_larger_size_before_it_is_sent(world):
    view = await world.om.submit(req())
    world.om.apply(_open(view))
    await world.om.modify(view.client_order_id, qty=150)
    assert world.broker.on_disk[-1] == (Pending.MODIFY, 150)  # a crash now: in doubt, at 150
    after = EventLog(world.path).latest_views("acc")[0]
    assert after.request.qty == 150


async def test_a_smaller_change_is_recorded_at_the_old_larger_size_until_answered(world):
    view = await world.om.submit(req(qty=150))
    world.om.apply(_open(view))
    await world.om.modify(view.client_order_id, qty=75)
    assert world.broker.on_disk[-1] == (Pending.MODIFY, 150)  # never understated meanwhile


async def test_a_change_that_cannot_be_recorded_is_not_sent(world):
    view = await world.om.submit(req())
    world.om.apply(_open(view))
    world.log.healthy = False
    world.log.last_error = OSError("disk gone")
    from laddrix.orders.model import OrderError

    with pytest.raises(OrderError, match="not sent: the change couldn't be recorded on disk"):
        await world.om.modify(view.client_order_id, qty=150)
    assert world.broker.modified == []
    assert world.om.get(view.client_order_id).pending is Pending.NONE  # free to try again


def _open(view):
    from laddrix.domain.orders import OrderUpdate

    return OrderUpdate(view.client_order_id, view.broker_order_id or "F1", S.OPEN)


# --- the log itself ------------------------------------------------------------------------------


async def test_a_stalled_disk_is_noticed_in_time_not_waited_for_forever(tmp_path):
    log = EventLog(tmp_path / "e.db")
    log.start()

    async def never():
        await asyncio.sleep(10)

    log.flush = never
    with pytest.raises(StorageError, match="didn't confirm a write within 0.05 s"):
        await log.durable(timeout_s=0.05)
    await log.aclose()


def test_memory_stays_bounded_when_the_disk_stops(tmp_path):
    log = EventLog(tmp_path / "e.db", max_backlog=10)  # never started: nothing is written
    for n in range(25):
        log.record_note("NOTE", "acc", f"note {n}")
    assert (log.backlog, log.dropped) == (10, 15)  # notes past the limit are dropped, counted
    ev = OrderEvent(EventKind.SUBMITTED, _now(), "acc", "MT1", None)
    log.record(ev)
    assert log.backlog == 11  # order events are always kept
    assert log.problem == "the audit log has fallen behind (11 events waiting)"


async def test_behind_means_refused_at_once(tmp_path):
    log = EventLog(tmp_path / "e.db", max_backlog=4)
    for n in range(3):
        log.record_note("NOTE", "acc", f"{n}")
    with pytest.raises(StorageError, match="fallen behind"):
        await log.persist(None)


def test_the_band_says_so_and_clears_when_writing_again(tmp_path):
    log = EventLog(tmp_path / "e.db")
    alerts = Alerts()
    log.healthy, log.last_error = False, OSError("No space left on device")
    audit_log(alerts, log)
    (a,) = alerts.active()
    assert a.key == "audit_log" and a.level == "ERROR"
    assert "new orders are refused until it writes again" in a.text
    log.healthy = True
    audit_log(alerts, log)
    assert alerts.active() == []


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def test_the_platform_wires_it_for_real_accounts_not_for_backtests(nifty_fut, tmp_path):
    from test_portfolio import Paper

    p = Paper(nifty_fut, tmp_path)
    assert p.rt.orders._durable == p.log.persist  # every order: on disk first
    await p.stop()
    from laddrix.marketdata.service import MarketData
    from laddrix.platform import Platform
    from laddrix.risk.kill_switch import KillSwitch

    store = InstrumentStore()
    replay = Platform(store, MarketData(), KillSwitch("firm"), None, durable_orders=False)
    assert replay.durable_orders is False
