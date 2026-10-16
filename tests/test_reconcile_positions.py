"""Position reconciliation: real differences alert, in-flight fills don't."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.broker.base import BrokerError, BrokerPosition
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import Product, Side
from mocktrade.positions.book import PositionBook
from mocktrade.positions.reconcile import PositionReconciler
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"
REL = "NSE:RELIANCE"
T0 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def book():
    return PositionBook("acc-1")


@pytest.fixture
def broker():
    return FakeBroker()


@pytest.fixture
def rec(book, broker, clock):
    return PositionReconciler(book.qty_by_product, broker, account_id="acc-1", clock=clock)


def kite_has(broker, **positions):
    broker.broker_positions = [
        BrokerPosition(FUT if name == "fut" else REL, qty, 0) for name, qty in positions.items()
    ]


async def test_equal_positions_raise_nothing(rec, book, broker):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    book.apply_fill(FUT, Product.MIS, Side.BUY, 75, 2501000)
    kite_has(broker, fut=150)  # brokers report net across products
    result = await rec.check()
    assert result.ok and result.alerts == () and result.suspected == ()


async def test_a_missing_fill_shows_up_as_an_alert(rec, book, broker, clock):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    kite_has(broker, fut=150)  # Kite has a fill we never received
    first = await rec.check()
    assert not first.ok and first.alerts == () and first.suspected == ((FUT, None),)
    clock.now += timedelta(seconds=10)
    second = await rec.check()
    (alert,) = second.alerts
    assert (alert.ours, alert.broker, alert.since) == (75, 150, T0)
    assert alert.message == (
        "NFO:NIFTY26OCTFUT: MockTrade shows +75, the broker shows +150 "
        "(a missed fill, or a trade made outside MockTrade)"
    )
    assert rec.alerts() == [alert]


async def test_a_fill_still_on_its_way_is_not_an_alert(rec, book, broker):
    kite_has(broker, fut=75)  # Kite filled; our fill message hasn't arrived yet
    assert (await rec.check()).suspected == ((FUT, None),)
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)  # ...now it has
    result = await rec.check()
    assert result.ok and result.alerts == () and result.suspected == ()


async def test_trades_made_outside_mocktrade_are_flagged(rec, broker):
    kite_has(broker, rel=10)  # bought in the broker's own app
    await rec.check()
    (alert,) = (await rec.check()).alerts
    assert (alert.instrument_id, alert.ours, alert.broker) == (REL, 0, 10)


async def test_alerts_clear_when_positions_match_again(rec, book, broker):
    heard = []
    rec.subscribe(lambda what, m: heard.append((what, m.ours, m.broker)))
    rec.subscribe(lambda what, m: 1 / 0)  # a broken screen changes nothing
    kite_has(broker, fut=75)
    await rec.check()
    await rec.check()
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    result = await rec.check()
    assert result.ok and rec.alerts() == []
    assert heard == [("raised", 0, 75), ("cleared", 0, 75)]


async def test_a_changing_difference_updates_the_alert_but_keeps_since(rec, book, broker, clock):
    kite_has(broker, fut=75)
    await rec.check()
    clock.now += timedelta(seconds=10)
    await rec.check()
    kite_has(broker, fut=150)
    clock.now += timedelta(minutes=3)
    (alert,) = (await rec.check()).alerts
    assert (alert.broker, alert.since) == (150, T0)


async def test_a_broker_that_cant_answer_is_not_a_mismatch(rec, broker):
    async def down():
        raise BrokerError("Kite /portfolio/positions: timed out")

    broker.positions = down
    result = await rec.check()
    assert not result.ok and result.error.endswith("timed out") and result.alerts == ()


async def test_run_checks_again_soon_after_a_first_sighting(rec, broker):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            raise asyncio.CancelledError

    rec._sleep = sleep
    kite_has(broker, fut=75)
    with pytest.raises(asyncio.CancelledError):
        await rec.run()
    assert waits == [10.0, 180.0, 180.0]  # quick re-check, then the normal pace


async def test_run_never_dies_on_an_unexpected_error(rec, broker):
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        raise RuntimeError("bug")

    async def sleep(_):
        if calls >= 2:
            raise asyncio.CancelledError

    broker.positions = broken
    rec._sleep = sleep
    with pytest.raises(asyncio.CancelledError):
        await rec.run()
    assert calls == 2


async def test_alerts_go_into_the_audit_trail(rec, broker, tmp_path, clock):
    log = EventLog(tmp_path / "events.db")
    log.start()
    rec.subscribe(
        lambda what, m: log.record_note(
            f"POSITION_MISMATCH_{what.upper()}",
            "acc-1",
            m.message,
            {"instrument_id": m.instrument_id, "ours": m.ours, "broker": m.broker},
            ts=clock(),
        )
    )
    kite_has(broker, fut=75)
    await rec.check()
    await rec.check()
    await log.flush()
    (note,) = log.events()
    assert note.kind == "POSITION_MISMATCH_RAISED"
    assert note.extra == {"instrument_id": FUT, "ours": 0, "broker": 75}
    assert log.latest_views("acc-1") == []  # notes are never mistaken for orders
    with pytest.raises(ValueError, match="order event kind"):
        log.record_note("FILL", "acc-1", "x")
    log.close()


def test_net_by_instrument_adds_products(book):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 150, 2501000)
    book.apply_fill(FUT, Product.MIS, Side.SELL, 75, 2501000)
    book.apply_fill(REL, Product.CNC, Side.BUY, 5, 140000)
    assert book.net_by_instrument() == {FUT: 75, REL: 5}


# --- #258: per instrument AND product -----------------------------------------------------------


def rows(*items):
    return [BrokerPosition(i, q, 0, product=p) for i, p, q in items]


async def test_the_audit_case_long_in_one_product_short_in_another(rec, book, broker):
    """Kite lists NRML +75 and MIS −75 for one contract. Comparing per instrument let the second
    row overwrite the first (broker −75 against our net 0): a false alarm. Per product, they
    match."""
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    book.apply_fill(FUT, Product.MIS, Side.SELL, 75, 2501000)
    broker.broker_positions = rows((FUT, Product.NRML, 75), (FUT, Product.MIS, -75))
    assert (await rec.check()).ok


async def test_a_difference_in_one_product_is_named_with_it(rec, book, broker, clock):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    book.apply_fill(FUT, Product.MIS, Side.SELL, 75, 2501000)
    broker.broker_positions = rows((FUT, Product.NRML, 75), (FUT, Product.MIS, -150))
    first = await rec.check()
    assert first.suspected == ((FUT, Product.MIS),)
    clock.now += timedelta(seconds=10)
    (alert,) = (await rec.check()).alerts
    assert (alert.key, alert.ours, alert.broker) == ((FUT, Product.MIS), -75, -150)
    assert alert.message.startswith(f"{FUT} MIS: MockTrade shows -75, the broker shows -150")
    # the net across products would have hidden it in the other direction too:
    broker.broker_positions = rows((FUT, Product.NRML, 0), (FUT, Product.MIS, 0))
    book_net = sum(book.qty_by_product().values())
    assert book_net == 0  # ours nets to zero, the broker's too: still two real differences
    clock.now += timedelta(seconds=10)
    await rec.check()
    clock.now += timedelta(seconds=10)
    keys = {m.key for m in (await rec.check()).alerts}
    assert keys == {(FUT, Product.MIS), (FUT, Product.NRML)}


async def test_a_row_without_its_product_is_compared_on_the_net(rec, book, broker):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    book.apply_fill(FUT, Product.MIS, Side.BUY, 75, 2501000)
    book.apply_fill(REL, Product.CNC, Side.BUY, 10, 140000)
    broker.broker_positions = [BrokerPosition(FUT, 150, 0),  # product not said
                               *rows((REL, Product.CNC, 10))]  # fmt: skip
    assert (await rec.check()).ok
    broker.broker_positions = [BrokerPosition(FUT, 75, 0), *rows((REL, Product.CNC, 10))]
    assert (await rec.check()).suspected == ((FUT, None),)  # the net differs


async def test_the_paper_broker_lists_one_row_per_product():
    from mocktrade.broker.paper import PaperBroker
    from mocktrade.marketdata.service import MarketData
    from mocktrade.refdata.store import InstrumentStore

    paper = PaperBroker(MarketData(), InstrumentStore())
    paper._book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    paper._book.apply_fill(FUT, Product.MIS, Side.SELL, 150, 2502000)
    got = {(p.instrument_id, p.product, p.qty, p.avg_price_paise) for p in await paper.positions()}
    assert got == {(FUT, Product.MIS, -150, 2502000), (FUT, Product.NRML, 75, 2501000)}


async def test_rows_without_a_product_add_up_and_our_own_rows_count(rec, book, broker):
    book.apply_fill(FUT, Product.NRML, Side.BUY, 150, 2501000)
    broker.broker_positions = [BrokerPosition(FUT, 75, 0), BrokerPosition(FUT, 75, 0)]
    assert (await rec.check()).ok  # two rows of one instrument: 75 + 75, never the last one
    book.apply_fill(REL, Product.CNC, Side.BUY, 10, 140000)  # the broker lists nothing for it
    assert (await rec.check()).suspected == ((REL, Product.CNC),)
