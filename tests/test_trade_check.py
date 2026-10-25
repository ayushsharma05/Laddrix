"""Orders against the broker's trade list (#352, part of #275).

The failure before: nothing compared our orders with the broker's tradebook. An order whose
value was worked out from a rounded average (a broker without exact averages) stayed a few
paise off for good, and a fill the order updates never reported was invisible."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, OrderUpdate, Product, Side
from laddrix.domain.portfolio import Trade
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind
from laddrix.orders.trade_check import TradeCheck, TradeGap, ledger
from laddrix.positions.book import PositionBook
from laddrix.refdata.store import InstrumentStore

S = OrderStatus
FUT = "NFO:NIFTY26OCTFUT"
T = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)


def trade(tid, qty, price, bid="F1"):
    return Trade(tid, bid, FUT, Side.BUY, Product.NRML, qty, price, T)


@pytest.fixture
def world(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    om = OrderManager("acc-1", FakeBroker(), store.get, ids=ClientIds("MT"))
    book, events, notes = PositionBook("acc-1"), [], []
    om.subscribe(events.append)
    om.subscribe(book.on_order_event)
    check = TradeCheck(om, note=lambda k, d, x: notes.append((k, d)))
    return om, book, events, notes, check


async def filled(om, qty, avg):
    view = await om.submit(OrderRequest("", "acc-1", "u", FUT, Side.BUY, 150, OrderType.LIMIT,
                                        Product.NRML, price_paise=2501010))  # fmt: skip
    om.apply(OrderUpdate(view.client_order_id, view.broker_order_id, S.PARTIAL if qty < 150
                         else S.FILLED, qty, avg))  # fmt: skip
    return om.get(view.client_order_id)


def test_the_ledger_counts_each_trade_id_once_and_a_corrected_trade_replaces_it():
    rows = [trade("T1", 50, 2501000), trade("T2", 100, 2501005), trade("T1", 50, 2501000)]
    assert ledger(rows) == {"F1": (150, 50 * 2501000 + 100 * 2501005)}
    assert ledger([*rows, trade("T2", 100, 2501006)])["F1"] == (150, 50 * 2501000 + 100 * 2501006)


async def test_the_trades_correct_a_value_worked_out_from_a_rounded_average(world):
    om, book, events, notes, check = world
    view = await filled(om, 150, 2501003)  # only a rounded average: 150 x 25,010.03
    assert view.filled_value_paise == 150 * 2501003
    exact = 50 * 2501000 + 100 * 2501005  # what the exchange trades say
    did = check.check([trade("T1", 50, 2501000), trade("T2", 100, 2501005)])
    assert did == [f"order {view.client_order_id}: the broker's trade list corrected what its "
                   "fills traded for by +₹0.50 (average now ₹25,010.03)"]  # fmt: skip
    assert om.get(view.client_order_id).filled_value_paise == exact
    (corrected,) = [e for e in events if e.kind is EventKind.CORRECTED]
    assert corrected.fill.value_paise == exact - 150 * 2501003  # ₹0.50 more
    assert book.positions()[0].buy_value_paise == exact
    assert check.check([trade("T1", 50, 2501000), trade("T2", 100, 2501005)]) == []  # once


async def test_a_quantity_that_differs_on_two_reads_is_named_never_changed(world):
    om, book, events, notes, check = world
    view = await filled(om, 150, 2501000)
    partial = [trade("T1", 100, 2501000)]
    assert check.check(partial) == [] and notes == []  # the list may lag: wait a read
    gap = TradeGap(view.client_order_id, "F1", 150, 100)
    assert check.check(partial) == [gap.words]
    assert notes == [("TRADES_DIFFER", gap.words)] and check.gaps == [gap]
    assert om.get(view.client_order_id).filled_qty == 150  # nothing changed by itself
    check.check(partial)
    assert len(notes) == 1  # named once
    check.check([*partial, trade("T2", 50, 2501000)])  # the list caught up
    assert check.gaps == [] and len(notes) == 1


async def test_orders_without_fills_or_a_broker_id_are_left_alone(world):
    om, _, events, notes, check = world
    await om.submit(OrderRequest("", "acc-1", "u", FUT, Side.BUY, 75, OrderType.LIMIT,
                                 Product.NRML, price_paise=2400000))  # fmt: skip
    assert check.check([]) == [] and check.check([]) == [] and notes == []


async def test_paper_trades_agree_with_paper_orders(nifty_fut):
    """The platform runs the check on each trade list: paper fills are exact, so nothing is
    corrected and nothing differs."""
    from test_portfolio import FUT as PFUT
    from test_portfolio import Paper

    p = Paper(nifty_fut)
    try:
        await p.buy(PFUT, 75, 2501010, Product.NRML)
        await p.rt.portfolio.refresh("trades")
        assert p.rt.trade_check is not None and p.rt.trade_check.gaps == []
        assert p.rt.trade_check.check(await p.paper.trades()) == []
        (real,) = await p.paper.trades()
        corrected = replace(real, price_paise=real.price_paise + 10)  # the tradebook says more

        async def tradebook():
            return [corrected]

        p.paper.trades = tradebook
        await p.rt.portfolio.refresh("trades")  # the platform checks each new trade list
        view = p.rt.orders.get(real.client_order_id)
        assert view.filled_value_paise == 75 * (real.price_paise + 10)
    finally:
        await p.stop()


async def test_charges_follow_the_corrected_value(world, nifty_fut):
    from laddrix.charges.model import ScheduleCharges
    from laddrix.charges.schedules import KITE_STANDARD
    from laddrix.charges.service import ChargesService

    om, _, _, _, check = world
    charges = ChargesService("acc-1", ScheduleCharges(KITE_STANDARD), om, lambda _i: nifty_fut,
                             clock=lambda: T)  # fmt: skip
    om.subscribe(charges.on_order_event)
    await filled(om, 150, 2501003)
    before = charges.bill()
    check.check([trade("T1", 150, 2601003)])  # the trades say ₹1,000 a unit more
    assert charges.bill().total_paise > before.total_paise  # worked out again, on the value


async def test_charges_are_worked_out_on_the_exact_value(world, nifty_fut):
    from fractions import Fraction

    from laddrix.charges.model import executed

    om, _, _, _, check = world
    view = await filled(om, 150, 2501003)
    check.check([trade("T1", 50, 2501000), trade("T2", 100, 2501005)])
    (done,) = executed([om.get(view.client_order_id)], lambda _i: nifty_fut)
    assert done.value_paise == Fraction(50 * 2501000 + 100 * 2501005)  # not 150 x the average
