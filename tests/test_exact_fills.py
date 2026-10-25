"""Exact fill economics (#275): what an order traded for, to the paisa.

The failure before: Kite reports an order's average price with fractions of a paisa
(100.0333…). The adapter rounded it to whole paise, and the order manager worked out each
new fill's price from the rounded averages. 50 bought at ₹100.00 then 100 at ₹100.05 came
out as 100 at ₹100.04: the position's cost was ₹1 too low, and every P&L after it was off.
A broker correction of the average with nothing new traded was dropped as a "duplicate".

    broker update ─► exact average x filled = the order's exact value (whole paise)
                  ─► new fill = value now − value before (kept exactly on the fill)
                  ─► same qty, different value: a CORRECTED event, positions follow
"""

from fractions import Fraction

import pytest

from laddrix.broker.fake import FakeBroker
from laddrix.broker.kite.orders import exact_paise, to_update
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from laddrix.orders.codec import fill_from_dict, fill_to_dict, view_from_dict, view_to_dict
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind
from laddrix.positions.book import PositionBook
from laddrix.refdata.store import InstrumentStore

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    om = OrderManager("acc-1", FakeBroker(), store.get, ids=ClientIds("MT"))
    book = PositionBook("acc-1")
    events = []
    om.subscribe(events.append)
    om.subscribe(book.on_order_event)
    return om, book, events


async def buy(om, qty=150):
    return await om.submit(OrderRequest("", "acc-1", "u", FUT, Side.BUY, qty, OrderType.LIMIT,
                                        Product.NRML, price_paise=2501000))  # fmt: skip


def kite(view, filled, average, status="OPEN"):
    """The update Kite's order list (or postback) gives: no fill price, only the average."""
    return to_update({"order_id": view.broker_order_id, "tag": view.client_order_id,
                            "status": status, "filled_quantity": filled,
                            "average_price": average, "order_timestamp": "2026-10-23 10:00:00"})  # fmt: skip  # noqa: E501


def test_kite_averages_keep_their_fractions():
    assert exact_paise("100.0333") == Fraction(1000333, 100)
    odd = to_update({"order_id": "B1", "tag": "MT1", "status": "SOMETHING NEW",
                     "filled_quantity": 3, "average_price": "100.0333"})  # fmt: skip
    assert odd.avg_price_exact == Fraction(1000333, 100)  # even a status we don't know
    assert exact_paise(25010.05) == 2501005
    assert exact_paise(None) is None and exact_paise("") is None


async def test_fills_worked_out_from_averages_add_up_to_the_exact_value(world):
    """The regression: 50 at ₹25,010.00 then 100 at ₹25,010.05."""
    om, book, events = world
    view = await buy(om)
    om.apply(kite(view, 50, "25010"))
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))  # (50 x 25010 + 100 x 25010.05) / 150
    fills = [e.fill for e in events if e.kind is EventKind.FILL]
    assert [(f.qty, f.value) for f in fills] == [(50, 50 * 2501000), (100, 100 * 2501005)]
    assert om.get(view.client_order_id).filled_value_paise == 50 * 2501000 + 100 * 2501005
    assert om.get(view.client_order_id).avg_price_paise == 2501003  # shown rounded
    (p,) = book.positions()
    assert p.buy_value_paise == 50 * 2501000 + 100 * 2501005  # to the paisa
    assert p.avg_price_paise == Fraction(50 * 2501000 + 100 * 2501005, 150)


async def test_one_update_covering_two_prices_keeps_the_exact_value(world):
    """150 filled in one go, at two prices: the one fill's value is exact, its price isn't."""
    om, book, events = world
    view = await buy(om)
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))
    (fill,) = [e.fill for e in events if e.kind is EventKind.FILL]
    assert (fill.qty, fill.price_paise, fill.value) == (150, 2501003, 375150500)
    (p,) = book.positions()
    assert p.buy_value_paise == 375150500
    assert p.avg_price_paise == Fraction(375150500, 150)
    book.apply_fill(FUT, Product.NRML, Side.SELL, 150, 2502000)
    assert book.positions()[0].realised_paise == 150 * 2502000 - 375150500  # ₹1,495.00


async def test_the_same_update_twice_or_late_changes_nothing(world):
    om, book, events = world
    view = await buy(om)
    om.apply(kite(view, 50, "25010"))
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))  # again: a duplicate
    om.apply(kite(view, 50, "25010"))  # late: less filled than we know
    assert len([e for e in events if e.kind in (EventKind.FILL, EventKind.CORRECTED)]) == 2
    assert book.positions()[0].buy_value_paise == 50 * 2501000 + 100 * 2501005


async def test_a_corrected_average_moves_the_position_once(world):
    om, book, events = world
    view = await buy(om)
    om.apply(kite(view, 150, "25010", "COMPLETE"))
    om.apply(kite(view, 150, "25010.02", "COMPLETE"))  # the broker corrects: ₹0.02 x 150 more
    (corrected,) = [e for e in events if e.kind is EventKind.CORRECTED]
    assert (corrected.fill.qty, corrected.fill.value_paise) == (0, 300)
    assert corrected.detail == "the broker corrected the average from ₹25,010.00 to ₹25,010.02"
    (p,) = book.positions()
    assert (p.qty, p.buy_value_paise, p.avg_price_paise) == (150, 150 * 2501002, 2501002)
    om.apply(kite(view, 150, "25010.02", "COMPLETE"))  # said again: no second correction
    assert len([e for e in events if e.kind is EventKind.CORRECTED]) == 1


def test_a_correction_after_the_position_closed_moves_the_realised_pnl():
    book = PositionBook("acc-1")
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    book.apply_fill(FUT, Product.NRML, Side.SELL, 75, 2502000)
    assert book.positions()[0].realised_paise == 75 * 1000
    book.correct(FUT, Product.NRML, Side.BUY, 750)  # the buys cost ₹7.50 more in all
    p = book.positions()[0]
    assert (p.qty, p.realised_paise, p.buy_value_paise) == (0, 75 * 1000 - 750, 75 * 2501000 + 750)


def test_a_rounded_average_that_agrees_is_no_correction():
    """A broker that rounds its average (the paper broker) after exact fills: no correction."""
    om = OrderManager("acc-1", FakeBroker(), lambda _i: None, ids=ClientIds("MT"))
    events = []
    om.subscribe(events.append)
    from laddrix.orders.manager import _filled_value, _Order

    order = _Order(OrderRequest("MT1", "acc-1", "u", FUT, Side.BUY, 150, OrderType.LIMIT,
                                Product.NRML, price_paise=1), None, None)  # fmt: skip
    order.filled_qty, order.filled_value_paise = 150, 75 * 2501000 + 75 * 2501005
    same = OrderUpdate("MT1", "B1", S.FILLED, 150, 2501003)  # 2501002.5 rounded half up
    assert _filled_value(order, same) == order.filled_value_paise
    other = OrderUpdate("MT1", "B1", S.FILLED, 150, 2501010)
    assert _filled_value(order, other) == 150 * 2501010


def test_the_exact_value_survives_a_restart():
    from laddrix.orders.model import Fill

    f = Fill("MT1", 100, 2501005, False, 100 * 2501005)
    assert fill_from_dict(fill_to_dict(f)) == f
    old = {"client_order_id": "MT1", "qty": 1, "price_paise": 5, "exact": True}  # before #275
    assert fill_from_dict(old).value == 5


async def test_an_old_saved_order_gets_its_value_from_its_average(world):
    om, _, _ = world
    view = await buy(om)
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))
    saved = view_to_dict(om.get(view.client_order_id))
    assert view_from_dict(saved).filled_value_paise == 50 * 2501000 + 100 * 2501005
    del saved["filled_value_paise"]  # saved before #275
    assert view_from_dict(saved).filled_value_paise == 150 * 2501003


async def test_a_restart_rebuilds_the_same_exact_position(nifty_fut, tmp_path):
    """Fills worked out from averages and a correction, written to the audit trail and read
    back as recovery does: the rebuilt position is the live one, to the paisa."""
    from laddrix.store.events import EventLog

    store = InstrumentStore()
    store.add(nifty_fut)
    log = EventLog(tmp_path / "events.db")
    log.start()
    om = OrderManager("acc-1", FakeBroker(), store.get, ids=ClientIds("MT"))
    live = PositionBook("acc-1")
    om.subscribe(log.record)
    om.subscribe(live.on_order_event)
    view = await buy(om)
    om.apply(kite(view, 50, "25010"))
    om.apply(kite(view, 150, "25010.0333", "COMPLETE"))
    om.apply(kite(view, 150, "25010.04", "COMPLETE"))  # corrected
    await log.flush()
    rebuilt = PositionBook("acc-1")
    for e in log.fills("acc-1"):
        rebuilt.replay(e.client_order_id, e.order.request, e.order.filled_qty, e.fill.qty,
                       e.fill.price_paise, e.fill.value_paise)  # fmt: skip
    assert [e.kind for e in log.fills("acc-1")] == ["FILL", "FILL", "CORRECTED"]
    assert rebuilt.positions()[0].buy_value_paise == live.positions()[0].buy_value_paise
    assert live.positions()[0].buy_value_paise == 150 * 2501004
    await log.aclose()
