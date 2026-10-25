"""F&O expiry day (#290): warnings before, refusals on the day, settlement after.

The failure before: nothing knew that a contract ends. On its expiry date at 15:30 a future or
option stops trading and the exchange settles every open position in it, but Laddrix kept
showing the position, carried it into the next day at its last price, offered it in search,
and would have sent orders in it (the exchange refuses them). Paper and live then disagreed
about positions, and reconciling stopped trading.

    the day before ─► info: expires 27 Oct at 15:30, cash or delivery
    expiry day     ─► "Expires today"; after a broker's cut-off only closing trades
    15:30          ─► expired: no order at all; search and the chain drop it
    next morning   ─► positions settle at the final settlement price (paper and our book)
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from laddrix.broker.paper import PaperBroker
from laddrix.domain.accounts import BrokerAccount
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from laddrix.domain.ticks import DepthLevel, Tick
from laddrix.marketdata.service import MarketData
from laddrix.marketdata.sim_universe import last_tuesday, monthly_expiries
from laddrix.options.chain import ChainService
from laddrix.orders.model import ClientIds, OrderError
from laddrix.platform import Platform
from laddrix.positions.book import PositionBook
from laddrix.refdata.expiry import Expiries, ExpiryRules, expiry_to_dict
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.kill_switch import KillSwitch
from laddrix.risk.limits import RiskLimits

pytestmark = pytest.mark.real_expiry  # these tests judge expiry by their own clock

IST = ZoneInfo("Asia/Kolkata")
K = InstrumentKind
DAY = date(2026, 10, 27)  # a Tuesday: the October monthly expiry

FUT_I = Instrument(Exchange.NFO, "NIFTY26OCTFUT", K.FUTURE, 10, 75, DAY, None, "NIFTY")
CE_I = Instrument(Exchange.NFO, "NIFTY26OCT25000CE", K.CALL, 5, 75, DAY, 2500000, "NIFTY")
PE_I = Instrument(Exchange.NFO, "NIFTY26OCT24000PE", K.PUT, 5, 75, DAY, 2400000, "NIFTY")
NOV_I = Instrument(Exchange.NFO, "NIFTY26NOVFUT", K.FUTURE, 10, 75, date(2026, 11, 24), None,
                   "NIFTY")  # fmt: skip
REL_I = Instrument(Exchange.NSE, "RELIANCE", K.EQUITY, 10, 1)
RFUT_I = Instrument(Exchange.NFO, "RELIANCE26OCTFUT", K.FUTURE, 10, 500, DAY, None, "RELIANCE")
RCE_I = Instrument(Exchange.NFO, "RELIANCE26OCT1400CE", K.CALL, 5, 500, DAY, 140000, "RELIANCE")
FUT, CE, PE, RFUT = FUT_I.id, CE_I.id, PE_I.id, RFUT_I.id
ALL = (FUT_I, CE_I, PE_I, NOV_I, REL_I, RFUT_I, RCE_I)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST).astimezone(UTC)


def store() -> InstrumentStore:
    s = InstrumentStore()
    for i in ALL:
        s.add(i)
    return s


def book(instrument_id: str, ltp: int, step: int = 10) -> Tick:
    return Tick(instrument_id, ltp, bids=(DepthLevel(ltp - step, 10_000, 2),),
                asks=(DepthLevel(ltp + step, 10_000, 1),))  # fmt: skip


# --- the facts the server publishes ----------------------------------------------------------


def test_a_contract_expires_at_the_close_on_its_expiry_date():
    e = Expiries(store())
    before = e.info(FUT_I, at(DAY - timedelta(days=1), 10))
    assert (before.expires_today, before.expired, before.settlement) == (False, False, "cash")
    assert (
        before.note
        == "Expires at 15:30 on 27 Oct 2026; settles in cash at the final settlement price"
    )
    day = e.info(FUT_I, at(DAY, 15, 29))
    assert (day.expires_today, day.expired) == (True, False)
    assert day.note.startswith("Expires today at 15:30 on 27 Oct 2026")
    assert day.expires_at == at(DAY, 15, 30)
    after = e.info(FUT_I, at(DAY, 15, 30))
    assert (after.expires_today, after.expired) == (False, True)
    assert (
        after.note == "NIFTY26OCTFUT expired at 15:30 on 27 Oct 2026: it can't be traded any more"
    )
    assert e.info(REL_I, at(DAY, 10)) is None  # shares don't expire


def test_stock_fo_settles_by_delivery_and_index_fo_in_cash():
    e = Expiries(store())
    assert e.physical(RFUT_I) and e.physical(RCE_I)
    assert not e.physical(FUT_I) and not e.physical(CE_I)
    info = e.info(RFUT_I, at(DAY, 10))
    assert info.settlement == "physical"
    assert "settles by delivery of the shares" in info.note


def test_a_brokers_cutoff_leaves_only_closing_trades_from_that_time():
    e = Expiries(store(), rules=ExpiryRules(physical_open_cutoff=time(13, 0)))
    assert not e.info(RFUT_I, at(DAY, 12, 59)).opening_closed
    closed = e.info(RFUT_I, at(DAY, 13, 0))
    assert closed.opening_closed and closed.opening_cutoff == at(DAY, 13, 0)
    assert "from 13:00 that day only closing trades are allowed" in closed.note
    assert not e.info(FUT_I, at(DAY, 14)).opening_closed  # no cut-off set for index F&O
    assert not e.info(RFUT_I, at(DAY - timedelta(days=1), 14)).opening_closed  # the day only


def test_the_final_settlement_price():
    e = Expiries(store())
    last = {FUT: 2520000, CE: 3000, PE: 15, "NSE:RELIANCE": 145000, RCE_I.id: 4000}.get
    assert e.settlement_price(FUT_I, last) == 2520000  # a future: its last price
    assert e.settlement_price(CE_I, last) == 20000  # in the money by ₹200 (the future's price)
    assert e.settlement_price(PE_I, last) == 0  # out of the money: worthless
    assert e.settlement_price(RCE_I, last) == 5000  # no stock future: the share's price
    assert e.settlement_price(CE_I, {CE: 3000}.get) == 3000  # nothing to work from: its own
    assert e.settlement_price(FUT_I, {}.get) is None


def test_the_facts_as_json():
    e = Expiries(store(), rules=ExpiryRules(cash_open_cutoff=time(15, 0)))
    body = expiry_to_dict(e.info(FUT_I, at(DAY, 15, 10)))
    assert body == {
        "expires_at": "2026-10-27T15:30:00+05:30",
        "settlement": "cash",
        "expires_today": True,
        "expired": False,
        "opening_cutoff": "2026-10-27T15:00:00+05:30",
        "opening_closed": True,
        "note": "Expires today at 15:30 on 27 Oct 2026; settles in cash at the final settlement "
        "price; from 15:00 that day only closing trades are allowed",
    }
    assert expiry_to_dict(None) is None


def test_search_and_the_chain_drop_a_contract_once_it_has_expired():
    s, clock = store(), Clock(at(DAY, 15, 29))
    e = Expiries(s, clock=clock)
    alive = lambda i: not e.expired(i)  # noqa: E731
    assert [i.symbol for i in s.search("NIFTY26", 10, keep=alive)] == [
        "NIFTY26NOVFUT", "NIFTY26OCTFUT", "NIFTY26OCT24000PE", "NIFTY26OCT25000CE"]  # fmt: skip
    chain = ChainService(s, MarketData(), clock=clock)
    assert chain.expiries("NIFTY") == [DAY]
    clock.now = at(DAY, 15, 30)
    assert [i.symbol for i in s.search("NIFTY26", 10, keep=alive)] == ["NIFTY26NOVFUT"]
    assert chain.expiries("NIFTY") == []  # only November's future is left: no options
    assert s.get(FUT) == FUT_I  # charts and recordings still find it by its id


def test_the_chain_says_when_its_expiry_ends():
    s, clock = store(), Clock(at(DAY, 11))
    md = MarketData()
    md.on_tick(book(FUT, 2501000))
    snap = ChainService(s, md, clock=clock).snapshot("NIFTY", DAY, around=2)
    assert snap["expiry_info"]["expires_today"] is True
    assert snap["expiry_info"]["settlement"] == "cash"


def test_the_demo_contracts_roll_with_the_calendar():
    assert last_tuesday(2026, 10) == DAY and last_tuesday(2026, 12) == date(2026, 12, 29)
    assert monthly_expiries(at(DAY, 15, 29)) == (DAY, date(2026, 11, 24))
    assert monthly_expiries(at(DAY, 15, 30)) == (date(2026, 11, 24), date(2026, 12, 29))
    assert monthly_expiries(at(date(2026, 12, 30), 9)) == (date(2027, 1, 26), date(2027, 2, 23))


def test_a_book_settles_at_any_price_even_zero():
    b = PositionBook("acc")
    b.apply_fill(PE, Product.NRML, Side.SELL, 75, 500)  # sold for ₹5
    assert b.settle(PE, Product.NRML, 0) == -75  # ends worthless: the seller keeps it all
    (v,) = b.positions()
    assert (v.qty, v.realised_paise) == (0, 75 * 500)
    assert b.settle(PE, Product.NRML, 0) == 0  # nothing left to settle
    b.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)
    with pytest.raises(ValueError):
        b.settle(FUT, Product.NRML, -1)


# --- the platform: refusals, settlement, a restart --------------------------------------------


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Desk:
    """A paper account on a clock, with every contract above priced."""

    def __init__(self, tmp_path, clock: Clock, *, rules: ExpiryRules | None = None,
                 recover: bool = False):  # fmt: skip
        from laddrix.store.events import EventLog

        self.clock, self.store, self.md = clock, store(), MarketData()
        for instrument_id, ltp in ((FUT, 2501000), (CE, 12000), (PE, 500), (NOV_I.id, 2514860),
                                   ("NSE:RELIANCE", 140000), (RFUT, 140500)):  # fmt: skip
            self.md.on_tick(book(instrument_id, ltp))
        self.log = EventLog(tmp_path / "events.db")
        self.log.start()
        self.platform = Platform(self.store, self.md, KillSwitch("firm"), self.log,
                                 expiry_rules=rules or ExpiryRules())  # fmt: skip
        self.paper = PaperBroker(self.md, self.store, latency_s=0.0, clock=clock)
        self.paper.start()
        self.rt = self.platform.add_account(
            BrokerAccount("acc", "paper", "P1", "ayush"), self.paper,
            ids=ClientIds("MT", self.log.last_counter("MT")),
            limits=RiskLimits(max_order_value_paise=10**12, max_position_lots=1000,
                              max_order_lots=1000, allow_option_selling=True),
            clock=clock, recover_on_start=recover,
        )  # fmt: skip
        self.task = asyncio.create_task(self.platform.run(reconcile=False))

    async def order(self, instrument_id: str, side: Side, qty: int, product=Product.NRML):
        """A marketable limit; waits for its fill unless it is refused."""
        ltp = self.md.latest(instrument_id).ltp_paise
        price = ltp + 10 if side is Side.BUY else ltp - 10  # the other side of book()
        view = await self.rt.orders.submit(OrderRequest("", "acc", "ayush", instrument_id, side,
                                                        qty, OrderType.LIMIT, product,
                                                        price_paise=price))  # fmt: skip
        if view.status is OrderStatus.REJECTED:
            return view  # the tests that expect a fill check it
        async with asyncio.timeout(5):
            while self.rt.orders.get(view.client_order_id).filled_qty < qty:
                await asyncio.sleep(0.01)
        return self.rt.orders.get(view.client_order_id)

    def move(self, instrument_id: str, ltp: int) -> None:
        self.md.on_tick(book(instrument_id, ltp))
        self.platform.mark(self.md.latest(instrument_id))

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.paper.aclose()
        await self.log.aclose()


@pytest.fixture
async def desk(tmp_path):
    d = Desk(tmp_path, Clock(at(DAY, 10)))
    yield d
    await d.stop()


async def test_an_expired_contract_takes_no_order_not_even_a_close(desk):
    await desk.order(FUT, Side.BUY, 75)
    desk.clock.now = at(DAY, 15, 31)
    refused = await desk.order(FUT, Side.SELL, 75)
    assert refused.status is OrderStatus.REJECTED
    assert refused.reason == (
        "NIFTY26OCTFUT expired at 15:30 on 27 Oct 2026: it can't be traded any more; "
        "its position settles at the final settlement price"
    )
    accepted = await desk.order(NOV_I.id, Side.SELL, 75)  # the next month trades as before
    assert accepted.status is OrderStatus.FILLED


async def test_after_the_brokers_cutoff_only_closing_trades_go(tmp_path):
    d = Desk(tmp_path, Clock(at(DAY, 12)), rules=ExpiryRules(physical_open_cutoff=time(13, 0)))
    try:
        await d.order(RFUT, Side.BUY, 1000)
        d.clock.now = at(DAY, 13, 30)
        more = await d.order(RFUT, Side.BUY, 500)
        assert more.status is OrderStatus.REJECTED
        assert more.reason == ("RELIANCE26OCTFUT expires today: from 13:00 only closing trades "
                               "are allowed (physical settlement)")  # fmt: skip
        assert (await d.order(RFUT, Side.SELL, 1500)).status is OrderStatus.REJECTED  # reverses
        assert (await d.order(RFUT, Side.SELL, 500)).status is OrderStatus.FILLED  # reduces
        assert (await d.order(FUT, Side.BUY, 75)).status is OrderStatus.FILLED  # cash: no cut-off
    finally:
        await d.stop()


async def test_growing_a_resting_order_after_the_cutoff_is_refused(tmp_path):
    d = Desk(tmp_path, Clock(at(DAY, 12)), rules=ExpiryRules(physical_open_cutoff=time(13, 0)))
    try:
        resting = await d.rt.orders.submit(OrderRequest("", "acc", "ayush", RFUT, Side.BUY, 1000,
                                                        OrderType.LIMIT, Product.NRML,
                                                        price_paise=139000))  # fmt: skip
        d.clock.now = at(DAY, 13, 30)
        with pytest.raises(OrderError, match="from 13:00 only closing trades are allowed"):
            await d.rt.orders.modify(resting.client_order_id, qty=1500)
        assert d.rt.orders.get(resting.client_order_id).request.qty == 1000
        await d.rt.orders.modify(resting.client_order_id, qty=500)  # less risk: allowed
    finally:
        await d.stop()


async def test_the_next_morning_settles_expired_positions_at_their_settlement_price(desk):
    await desk.order(FUT, Side.BUY, 75)  # at 25,010.10
    await desk.order(CE, Side.BUY, 75)  # at 120.05
    await desk.order(PE, Side.SELL, 75)  # at 4.90
    await desk.order(NOV_I.id, Side.BUY, 75)  # November: not expiring
    desk.move(FUT, 2520000)  # the close on expiry day: 25,200
    desk.move(CE, 20500)
    desk.move(PE, 5)
    desk.clock.now = at(DAY + timedelta(days=1), 8, 45)
    words = desk.platform.roll_day()["acc"]
    assert words.endswith("1 position(s) carried at the last price, 0 day order(s) expired, "
                          "3 expired contract(s) settled")  # fmt: skip
    assert [(v.instrument_id, v.qty) for v in desk.rt.positions.positions()] == [(NOV_I.id, 75)]
    assert desk.rt.positions.day_pnl_paise() == 0  # the settlement belongs to expiry day
    paper = {p.instrument_id: p.qty for p in await desk.paper.positions()}
    assert paper == {FUT: 0, CE: 0, PE: 0, NOV_I.id: 75}  # the paper exchange agrees
    await desk.log.flush()
    notes = {e.extra["instrument_id"]: e.extra for e in desk.log.events(account_id="acc")
             if e.kind == "EXPIRY_SETTLED"}  # fmt: skip
    assert {k: (v["qty"], v["price_paise"], v["settlement"]) for k, v in notes.items()} == {
        FUT: (75, 2520000, "cash"),  # the future at its last price
        CE: (75, 20000, "cash"),  # the call at 25,200 − 25,000: ₹200
        PE: (-75, 0, "cash"),  # the put far below: worthless
    }


async def test_a_restart_after_expiry_brings_no_expired_position_back(tmp_path):
    """Two ways: settled at the morning's roll (noted in the audit trail), or the server was
    off over expiry (the restart settles it)."""
    clock = Clock(at(DAY, 10))
    d = Desk(tmp_path, clock)
    await d.order(FUT, Side.BUY, 75)
    await d.order(NOV_I.id, Side.BUY, 75)
    d.move(FUT, 2520000)
    clock.now = at(DAY + timedelta(days=1), 8, 45)
    d.platform.roll_day()
    await d.stop()
    clock.now = at(DAY + timedelta(days=1), 9, 0)
    again = Desk(tmp_path, clock, recover=True)
    try:
        async with asyncio.timeout(5):
            await again.rt.recovery.finished.wait()
        assert again.rt.recovery.state == "ok", again.rt.recovery.reason
        assert [(v.instrument_id, v.qty) for v in again.rt.positions.positions()] == [
            (NOV_I.id, 75)]  # fmt: skip
        assert again.paper._earlier_pnl_paise == 75 * (2520000 - 2501010)  # the noted price
    finally:
        await again.stop()


async def test_a_server_off_over_expiry_settles_on_restart(tmp_path):
    clock = Clock(at(DAY, 10))
    d = Desk(tmp_path, clock)
    await d.order(CE, Side.BUY, 75)
    await d.stop()
    clock.now = at(DAY + timedelta(days=1), 9, 0)  # no roll: the server was off
    again = Desk(tmp_path, clock, recover=True)
    try:
        async with asyncio.timeout(5):
            await again.rt.recovery.finished.wait()
        assert again.rt.recovery.state == "ok", again.rt.recovery.reason
        assert "1 expired positions settled" in again.rt.recovery.reason
        assert again.rt.positions.positions() == []
        assert {p.instrument_id: p.qty for p in await again.paper.positions()} in ({}, {CE: 0})
    finally:
        await again.stop()
