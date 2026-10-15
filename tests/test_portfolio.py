"""Funds, holdings and the tradebook from the broker (K10.1, #213)."""

import asyncio
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import httpx
import pytest
from test_kite_broker import FakeKiteRest, error, no_sleep, ok

from mocktrade.broker.base import BrokerError, FundsSource, HoldingsSource, TradesSource
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.kite.broker import KiteBroker
from mocktrade.broker.kite.http import KiteHttp
from mocktrade.broker.paper import PaperBroker
from mocktrade.broker.paper_funds import Exposure, PaperMargins, paper_funds, used_margin
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.portfolio import Funds, FundsLine, Holding, Trade
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.portfolio.service import PortfolioError, PortfolioService, snapshot_to_dict
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"
REL = "NSE:RELIANCE"
EQ = Instrument(Exchange.NSE, "RELIANCE", InstrumentKind.EQUITY, tick_paise=10, lot_size=1)
CALL = Instrument(Exchange.NFO, "NIFTY26OCT25500CE", InstrumentKind.CALL, 5, 75,
                  strike_paise=2550000, underlying="NIFTY")  # fmt: skip


def book(instrument_id, ltp, close=None):
    return Tick(instrument_id, ltp, close_paise=close,
                bids=(DepthLevel(ltp - 10, 10_000, 2),),
                asks=(DepthLevel(ltp + 10, 10_000, 1),))  # fmt: skip


# --- Kite: /portfolio/holdings, /user/margins, /trades ---------------------------------------


@pytest.fixture
def kite():
    return FakeKiteRest()


@pytest.fixture
def broker(kite):
    http = KiteHttp("key", "token", transport=httpx.MockTransport(kite.handler))
    return KiteBroker(http, kite_user_id="AB1234", sleep=no_sleep)


async def test_kite_holdings_in_paise_with_t1_and_unreadable_rows_skipped(broker, kite):
    kite.reply("GET", "/portfolio/holdings", ok([
        {"exchange": "NSE", "tradingsymbol": "RELIANCE", "quantity": 10, "t1_quantity": 2,
         "used_quantity": 3, "average_price": 2450.555, "last_price": 2501.1,
         "close_price": 2490},
        {"exchange": "NSE", "quantity": 1},  # no symbol: skipped, never guessed
    ]))  # fmt: skip
    (h,) = await broker.holdings()
    assert (h.instrument_id, h.qty, h.t1_qty, h.used_qty) == (REL, 10, 2, 3)
    assert (h.avg_price_paise, h.ltp_paise, h.close_paise) == (245056, 250110, 249000)
    assert h.invested_paise == 12 * 245056 and h.pnl_paise == 12 * (250110 - 245056)
    assert h.day_change_paise == 12 * (250110 - 249000)


async def test_kite_margins_as_kite_gives_them(broker, kite):
    kite.reply("GET", "/user/margins", ok({
        "equity": {"enabled": True, "net": 99725.05,
                   "available": {"opening_balance": 100000, "cash": 100000, "collateral": 0,
                                 "live_balance": 99725.05},
                   "utilised": {"debits": 274.95, "span": 0, "option_premium": 274.95,
                                "m2m_unrealised": -12.5}},
        "commodity": {"enabled": False, "net": 0},
    }))  # fmt: skip
    (f,) = await broker.funds()
    assert (f.segment, f.available_paise, f.used_paise, f.opening_paise) == (
        "equity", 9972505, 27495, 10000000)  # fmt: skip
    assert f.available_lines[0] == FundsLine("Opening balance", 10000000)
    assert FundsLine("Options premium", 27495) in f.used_lines
    assert FundsLine("Unrealised M2M", -1250) in f.used_lines  # Kite's sign, untouched
    assert not f.estimate


async def test_kite_trades_oldest_first_and_failures_are_clear(broker, kite):
    kite.reply("GET", "/trades", ok([
        {"trade_id": "2", "order_id": "9", "exchange": "NFO", "tradingsymbol": "NIFTY26OCTFUT",
         "transaction_type": "SELL", "product": "NRML", "quantity": 75,
         "average_price": 25010.1, "fill_timestamp": "2026-10-15 10:00:02"},
        {"trade_id": "1", "order_id": "9", "exchange": "NFO", "tradingsymbol": "NIFTY26OCTFUT",
         "transaction_type": "SELL", "product": "NRML", "quantity": 75,
         "average_price": 25010, "fill_timestamp": "2026-10-15 10:00:01"},
        {"trade_id": "3", "transaction_type": "HOLD"},
    ]))  # fmt: skip
    trades = await broker.trades()
    assert [(t.trade_id, t.price_paise, t.side) for t in trades] == [
        ("1", 2501000, Side.SELL), ("2", 2501010, Side.SELL)]  # fmt: skip
    assert trades[0].ts.isoformat() == "2026-10-15T10:00:01+05:30"
    kite.reply("GET", "/user/margins", error(403, "TokenException", "Session expired"))
    with pytest.raises(BrokerError, match="Session expired"):
        await broker.funds()


def test_which_brokers_offer_which_ports(broker):
    assert all(isinstance(broker, p) for p in (FundsSource, HoldingsSource, TradesSource))
    fake = FakeBroker()
    assert not any(isinstance(fake, p) for p in (FundsSource, HoldingsSource, TradesSource))


# --- paper money, by hand -------------------------------------------------------------------

RATES = PaperMargins()


def test_a_delivery_buy_or_an_option_bought_costs_exactly_what_was_paid():
    for instrument, product in ((EQ, Product.CNC), (CALL, Product.NRML)):
        # bought 10 at 100.00, now 110.00: P&L +100.00, the margin is its value
        held = [Exposure(instrument, product, 10, 11000)]
        f = paper_funds(starting_paise=100_000_00, earlier_pnl_paise=0, holdings_cost_paise=0,
                        realised_paise=0, unrealised_paise=10 * 1000, exposures=held,
                        rates=RATES)  # fmt: skip
        assert f.available_paise == 100_000_00 - 10 * 10000, instrument
        assert f.estimate


def test_margin_rates_by_kind():
    used, credit = used_margin(RATES, [
        Exposure(EQ, Product.MIS, 100, 250000),  # 20% of ₹2.5 lakh
        Exposure(_fut(), Product.NRML, 75, 2501000),  # 12% of the contract
        Exposure(CALL, Product.NRML, -75, 12000),  # sold: 12% of the strike value
    ])  # fmt: skip
    assert used == {"MIS margin": Fraction(100 * 250000 * 20, 100),
                    "SPAN + exposure": Fraction(75 * 2501000 * 12, 100)
                    + Fraction(75 * 2550000 * 12, 100)}  # fmt: skip
    assert credit == 0


def test_holdings_sold_today_are_a_credit_not_margin():
    # 10 held shares sold at 110.00 today; now 105.00: the sale brought in 1,100.00
    f = paper_funds(starting_paise=0, earlier_pnl_paise=0, holdings_cost_paise=0,
                    realised_paise=0, unrealised_paise=10 * (11000 - 10500),
                    exposures=[Exposure(EQ, Product.CNC, -10, 10500)], rates=RATES)  # fmt: skip
    assert f.available_paise == 10 * 11000
    assert FundsLine("Holdings sold today", 10 * 10500) in f.available_lines
    assert f.used_paise == 0


def test_working_orders_block_the_worst_case_and_closing_orders_nothing():
    fut = _fut()
    m = lambda q, p: Fraction(abs(q) * p * 12, 100)  # noqa: E731
    long_ = Exposure(fut, Product.NRML, 75, 2501000)
    closing = Exposure(fut, Product.NRML, 75, 2501000, sell_leaves=75, sell_price=2500000)
    adding = Exposure(fut, Product.NRML, 75, 2501000, buy_leaves=75, buy_price=2502000)
    crossing = Exposure(fut, Product.NRML, 75, 2501000, sell_leaves=150, sell_price=2500000)
    assert used_margin(RATES, [long_])[0]["SPAN + exposure"] == m(75, 2501000)
    assert used_margin(RATES, [closing])[0]["SPAN + exposure"] == m(75, 2501000)
    assert used_margin(RATES, [adding])[0]["SPAN + exposure"] == m(75, 2501000) + m(75, 2502000)
    assert used_margin(RATES, [crossing])[0]["SPAN + exposure"] == m(75, 2501000)  # max(75, 75)
    both = Exposure(fut, Product.NRML, 0, 2501000, buy_leaves=75, sell_leaves=150,
                    buy_price=2501000, sell_price=2500000)  # fmt: skip
    assert used_margin(RATES, [both])[0]["SPAN + exposure"] == m(150, 2500000)
    cnc_sell = Exposure(EQ, Product.CNC, 0, 10000, sell_leaves=10, sell_price=10000)
    assert used_margin(RATES, [cnc_sell]) == ({}, 0)  # no credit until it fills


def test_opening_cash_is_after_earlier_pnl_and_what_holdings_cost():
    f = paper_funds(starting_paise=10_00_000_00, earlier_pnl_paise=5_000_00,
                    holdings_cost_paise=2_45_056_00, realised_paise=0, unrealised_paise=0,
                    exposures=[], rates=RATES)  # fmt: skip
    assert f.opening_paise == f.available_paise == 10_00_000_00 + 5_000_00 - 2_45_056_00
    assert [line.label for line in f.used_lines] == [
        "SPAN + exposure", "Options premium", "Delivery margin", "MIS margin"]  # fmt: skip


def _fut():
    return Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, 10, 75)


# --- the paper broker -----------------------------------------------------------------------


class Paper:
    def __init__(self, nifty_fut, tmp_path=None, *, recover=False, clock=None):
        self.store, self.md = InstrumentStore(), MarketData()
        for i in (nifty_fut, EQ):
            self.store.add(i)
        self.md.on_tick(book(FUT, 2501000, close=2490000))
        self.md.on_tick(book(REL, 250000, close=248000))
        self.log = EventLog(tmp_path / "events.db") if tmp_path else None
        if self.log:
            self.log.start()
        self.platform = Platform(self.store, self.md, KillSwitch("firm"), self.log)
        self.paper = PaperBroker(
            self.md, self.store, latency_s=0.0, **({"clock": clock} if clock else {})
        )
        self.paper.start()
        self.rt = self.platform.add_account(
            BrokerAccount("acc", "paper", "P1", "ayush"), self.paper,
            ids=ClientIds("MT", self.log.last_counter("MT") if self.log else 0),
            limits=RiskLimits(max_order_value_paise=10**12, max_position_lots=1000,
                              max_order_lots=1000),
            recover_on_start=recover, **({"clock": clock} if clock else {}),
        )  # fmt: skip
        self.task = asyncio.create_task(self.platform.run(reconcile=False))

    async def ready(self):
        if self.rt.recovery is not None:
            async with asyncio.timeout(5):
                await self.rt.recovery.finished.wait()
            assert self.rt.recovery.state == "ok", self.rt.recovery.reason

    async def buy(self, instrument_id, qty, price, product, side=Side.BUY):
        view = await self.rt.orders.submit(OrderRequest("", "acc", "ayush", instrument_id, side,
                                                        qty, OrderType.LIMIT, product,
                                                        price_paise=price))  # fmt: skip
        async with asyncio.timeout(5):
            while self.rt.orders.get(view.client_order_id).filled_qty < qty:
                await asyncio.sleep(0.01)
        return view

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.paper.aclose()
        if self.log:
            await self.log.aclose()


async def test_paper_trades_funds_and_no_holdings_on_the_first_day(nifty_fut):
    p = Paper(nifty_fut)
    view = await p.buy(FUT, 75, 2501010, Product.NRML)  # takes the ask at 25,010.10
    (t,) = await p.paper.trades()
    assert (t.client_order_id, t.side, t.qty, t.price_paise) == (view.client_order_id, Side.BUY,
                                                                75, 2501010)  # fmt: skip
    (f,) = await p.paper.funds()
    margin = round(Fraction(75 * 2501000 * 12, 100))  # marked at the last price
    assert f.used_paise == margin
    assert f.available_paise == 10_00_000_00 + 75 * (2501000 - 2501010) - margin
    assert await p.paper.holdings() == []  # today's delivery buys are positions until tomorrow
    await p.stop()


async def test_yesterdays_delivery_buy_is_a_holding_after_a_restart(tmp_path, nifty_fut):
    yesterday = lambda: datetime.now(UTC) - timedelta(days=1)  # noqa: E731
    day = Paper(nifty_fut, tmp_path, recover=True, clock=yesterday)
    await day.ready()
    await day.buy(REL, 10, 250010, Product.CNC)
    await day.buy(FUT, 75, 2501010, Product.NRML)
    await day.stop()

    again = Paper(nifty_fut, tmp_path, recover=True)
    await again.ready()
    assert "1 carried positions, 1 holdings" in again.rt.recovery.reason
    assert [(p.instrument_id, p.qty) for p in again.rt.positions.positions()] == [(FUT, 75)]
    (h,) = await again.paper.holdings()
    assert (h.instrument_id, h.qty, h.avg_price_paise, h.ltp_paise) == (REL, 10, 250010, 250000)
    assert h.day_change_paise == 10 * (250000 - 248000)
    (f,) = await again.paper.funds()
    # yesterday: the future settled at the close (24,900.00), the shares cost 25,001.00
    earlier = 75 * (2490000 - 2501010)
    assert f.opening_paise == 10_00_000_00 + earlier - 10 * 250010
    # selling 4 held shares today: a credit, and the holding shows them as used
    await again.buy(REL, 4, 249990, Product.CNC, side=Side.SELL)
    (h,) = await again.paper.holdings()
    assert (h.qty, h.used_qty) == (10, 4)
    (f,) = await again.paper.funds()
    assert FundsLine("Holdings sold today", 4 * 250000) in f.available_lines
    await again.stop()


# --- the service ----------------------------------------------------------------------------


class Reads(FakeBroker):
    """A fake broker with funds and trades (no holdings), counting the calls."""

    def __init__(self):
        super().__init__()
        self.calls = {"funds": 0, "trades": 0}
        self.fail: str | None = None
        self.gate: asyncio.Event | None = None
        self.available = 100

    async def funds(self):
        self.calls["funds"] += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise BrokerError(self.fail)
        return [Funds("equity", self.available, 0, 100)]

    async def trades(self):
        self.calls["trades"] += 1
        return [Trade("1", "B7", FUT, Side.BUY, Product.NRML, 75, 2501000, None),
                Trade("2", "X9", FUT, Side.SELL, Product.NRML, 75, 2501000, None)]  # fmt: skip


def service(broker, **kw):
    md = MarketData()
    s = PortfolioService("acc", broker, md, client_id_of={"B7": "MT1"}.get, **kw)
    seen = []
    s.subscribe(lambda account_id, snap: seen.append((account_id, snap.kind, snap.error)))
    return s, md, seen


async def test_a_failed_read_keeps_the_last_figures_and_says_why():
    broker = Reads()
    s, _, seen = service(broker)
    first = await s.read("funds")
    assert first.items[0].available_paise == 100 and first.error == "" and first.as_of
    broker.fail = "Kite /user/margins: Session expired"
    stale = await s.read("funds", fresh=True)
    assert stale.items == first.items and stale.as_of == first.as_of
    assert stale.error == "Kite /user/margins: Session expired"
    broker.fail = None
    await s.read("funds", fresh=True)  # same figures, error cleared: pushed
    await s.read("funds", fresh=True)  # nothing changed: not pushed
    assert seen == [("acc", "funds", ""), ("acc", "funds", "Kite /user/margins: Session expired"),
                    ("acc", "funds", "")]  # fmt: skip


async def test_one_read_at_a_time_and_ports_the_broker_lacks():
    broker = Reads()
    broker.gate = asyncio.Event()
    s, _, _ = service(broker)
    reads = [asyncio.create_task(s.read("funds", fresh=True)) for _ in range(5)]
    await asyncio.sleep(0.01)
    broker.gate.set()
    await asyncio.gather(*reads)
    assert broker.calls["funds"] == 1
    assert s.offered == ("funds", "trades")
    with pytest.raises(PortfolioError, match="fake doesn't report holdings"):
        await s.read("holdings")
    assert PortfolioService("acc", FakeBroker(), MarketData()).offered == ()


async def test_trades_carry_our_order_ids_and_fills_refresh_once_after_a_burst():
    from mocktrade.orders.model import EventKind, OrderEvent

    broker = Reads()
    s, _, _ = service(broker, after_fill_s=0.02)
    trades = (await s.read("trades")).items
    assert [t.client_order_id for t in trades] == ["MT1", ""]  # X9 was placed elsewhere
    data = snapshot_to_dict("acc", s.snapshot("trades"))
    assert data["items"][0]["value_paise"] == 75 * 2501000 and data["kind"] == "trades"
    for _ in range(3):
        s.on_order_event(OrderEvent(EventKind.FILL, datetime.now(UTC), "acc", "MT1", None))
    await asyncio.sleep(0.1)
    assert broker.calls == {"funds": 1, "trades": 2}  # one refresh after the last fill


async def test_holdings_are_marked_with_live_prices():
    class Held(FakeBroker):
        async def holdings(self):
            return [Holding(REL, 10, 245000, ltp_paise=250000, close_paise=248000)]

    s, md, seen = service(Held())
    (h,) = (await s.read("holdings")).items
    assert h.ltp_paise == 250000
    md.on_tick(Tick(REL, 251000, close_paise=248000))
    s.mark()
    (h,) = s.snapshot("holdings").items
    assert h.ltp_paise == 251000 and h.pnl_paise == 10 * (251000 - 245000)
    s.mark()  # no new price: nothing pushed
    assert len(seen) == 2


# --- REST and the stream --------------------------------------------------------------------


async def test_the_portfolio_api(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)
    rt = w.platform.account("acc-ayush")
    broker = Reads()
    rt.portfolio = PortfolioService("acc-ayush", broker, w.platform.market_data)
    c = await w.logged_in()
    r = await c.get("/api/accounts/acc-ayush/funds")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "funds" and body["items"][0]["available_paise"] == 100
    assert body["error"] == "" and body["as_of"]
    broker.available = 250
    assert (await c.get("/api/accounts/acc-ayush/funds")).json()["items"][0][
        "available_paise"
    ] == 100
    fresh = await c.get("/api/accounts/acc-ayush/funds?fresh=true")
    assert fresh.json()["items"][0]["available_paise"] == 250
    r = await c.get("/api/accounts/acc-ayush/holdings")
    assert r.status_code == 404 and "doesn't report holdings" in r.json()["detail"]
    r = await c.get("/api/accounts/acc-nobody/trades")
    assert r.status_code == 404
    await c.aclose()
    w.log.close()
