"""After a restart: orders, fills and positions rebuilt and checked before trading (#206)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.broker.base import BrokerError, BrokerPosition
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
)
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.recovery import PAPER_GONE, session_start
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"
BID, ASK = 2501000, 2501010
ACCOUNT = BrokerAccount("acc", "paper", "P1", "ayush")
LIMITS = RiskLimits(max_order_value_paise=10**12)


def book(close=None):
    return Tick(
        FUT, BID, close_paise=close,
        bids=(DepthLevel(BID, 1500, 2),), asks=(DepthLevel(ASK, 1500, 1),),
    )  # fmt: skip


def request(side=Side.BUY, qty=150, price=ASK):
    return OrderRequest("", "acc", "ayush", FUT, side, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=price)  # fmt: skip


class Server:
    """One run of the server: platform, broker and audit trail, closed by `stop`."""

    def __init__(self, tmp_path, nifty_fut, broker=None, *, recover=True, clock=None):
        self.store, self.md = InstrumentStore(), MarketData()
        self.store.add(nifty_fut)
        self.md.on_tick(book(close=2490000))
        self.log = EventLog(tmp_path / "events.db")
        self.log.start()
        self.platform = Platform(self.store, self.md, KillSwitch("firm"), self.log)
        self.paper = None
        if broker is None:
            broker = self.paper = PaperBroker(self.md, self.store, latency_s=0.0)
            self.paper.start()
        self.broker = broker
        extra = {"clock": clock} if clock else {}
        self.runtime = self.platform.add_account(
            ACCOUNT, broker, ids=ClientIds("MT", self.log.last_counter("MT")), limits=LIMITS,
            recover_on_start=recover, **extra,
        )  # fmt: skip
        self.task = asyncio.create_task(self.platform.run(reconcile=False))

    async def ready(self, state="ok"):
        gate = self.runtime.recovery
        async with asyncio.timeout(5):
            while gate.state != state:
                await asyncio.sleep(0.01)
        return gate

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        if self.paper:
            await self.paper.aclose()
        await self.log.aclose()


async def filled(runtime, view, qty=None):
    async with asyncio.timeout(5):
        while (v := runtime.orders.get(view.client_order_id)).filled_qty < (
            qty or view.request.qty
        ):
            await asyncio.sleep(0.01)
    return v


# --- paper: rebuilt from the audit trail ---


async def test_a_restarted_paper_account_comes_back_as_it_was(tmp_path, nifty_fut):
    day = Server(tmp_path, nifty_fut)
    await day.ready()
    await filled(day.runtime, await day.runtime.orders.submit(request(qty=150)))
    await filled(day.runtime, await day.runtime.orders.submit(request(Side.SELL, 75, BID)))
    resting = await day.runtime.orders.submit(request(qty=75, price=2400000))  # far below
    before = day.runtime.positions.positions()[0]
    await day.stop()

    again = Server(tmp_path, nifty_fut)
    gate = await again.ready()
    rt = again.runtime
    after = rt.positions.positions()[0]
    assert (after.qty, after.realised_paise, after.buy_qty, after.sell_qty) == (
        before.qty,
        before.realised_paise,
        before.buy_qty,
        before.sell_qty,
    )  # same position and P&L: every fill once
    gone = rt.orders.get(resting.client_order_id)
    assert gone.status is OrderStatus.CANCELLED and gone.reason == PAPER_GONE
    assert "3 orders, 2 fills" in gate.reason and "1 paper orders closed" in gate.reason
    assert [(p.instrument_id, p.qty) for p in await again.paper.positions()] == [(FUT, 75)]
    new = await rt.orders.submit(request(qty=75))
    assert new.status is not OrderStatus.REJECTED  # trading again
    assert new.client_order_id not in {resting.client_order_id}  # ids continue
    await again.log.flush()
    kinds = [e.kind for e in again.log.events(account_id="acc") if e.kind.startswith("RECOVERY")]
    assert kinds == ["RECOVERY_OK", "RECOVERY_OK"]  # one for each start
    await again.stop()


async def test_positions_from_earlier_days_are_carried_at_the_close(tmp_path, nifty_fut):
    yesterday = lambda: datetime.now(UTC) - timedelta(days=1)  # noqa: E731
    day = Server(tmp_path, nifty_fut, clock=yesterday)
    await day.ready()
    await filled(day.runtime, await day.runtime.orders.submit(request(qty=150)))
    await day.stop()

    again = Server(tmp_path, nifty_fut)
    await again.ready()
    (p,) = again.runtime.positions.positions()
    assert p.qty == 150 and p.avg_price_paise == 2490000  # yesterday's close
    assert (p.buy_qty, p.realised_paise) == (0, 0)  # not today's trading
    assert again.runtime.positions.day_pnl_paise() == (BID - 2490000) * 150
    await again.stop()


# --- the gate ---


async def test_no_new_orders_until_recovered_but_cancels_go(tmp_path, nifty_fut):
    broker = FakeBroker()
    broker.positions = _failing  # the broker can't be asked yet (Kite before the login)
    s = Server(tmp_path, nifty_fut, broker=broker)
    gate = await s.ready("recovering")
    async with asyncio.timeout(2):
        while "waiting for the broker" not in gate.reason:
            await asyncio.sleep(0.01)
    refused = await s.runtime.orders.submit(request())
    assert refused.status is OrderStatus.REJECTED
    assert refused.reason.startswith("trading is paused after a restart: waiting for the broker")
    assert await gate.admit_cancel(refused) is None
    await s.stop()


async def _failing():
    raise BrokerError("Kite /portfolio/positions: not logged in")


async def test_recovery_waits_for_the_broker_then_completes(tmp_path, nifty_fut):
    broker = FakeBroker()
    real = broker.positions
    broker.positions = _failing
    s = Server(tmp_path, nifty_fut, broker=broker)
    s.task.cancel()  # rerun with a short retry
    await asyncio.gather(s.task, return_exceptions=True)
    gate = s.runtime.recovery
    run = asyncio.create_task(s.platform._recover(s.runtime, retry_s=0.05))
    async with asyncio.timeout(2):
        while "waiting for the broker" not in gate.reason:
            await asyncio.sleep(0.01)
    broker.positions = real  # the trader logs in
    await asyncio.wait_for(run, 2)
    assert gate.state == "ok"
    s.task = asyncio.create_task(asyncio.sleep(0))
    await s.stop()


# --- a broker that keeps its orders (Kite) ---


async def _day_with_a_working_order(tmp_path, nifty_fut):
    broker = FakeBroker()
    s = Server(tmp_path, nifty_fut, broker=broker, recover=False)
    working = await s.runtime.orders.submit(request(qty=150, price=2400000))
    await s.stop()
    return working


async def test_a_working_order_the_broker_doesnt_list_keeps_trading_shut(tmp_path, nifty_fut):
    working = await _day_with_a_working_order(tmp_path, nifty_fut)
    s = Server(tmp_path, nifty_fut, broker=FakeBroker())  # the broker lists nothing
    gate = await s.ready("failed")
    assert f"order {working.client_order_id} (BUY 150 {FUT}) was working" in gate.reason
    refused = await s.runtime.orders.submit(request(qty=75))
    assert refused.status is OrderStatus.REJECTED and "paused after a restart" in refused.reason
    await s.log.flush()
    assert any(e.kind == "RECOVERY_FAILED" for e in s.log.events(account_id="acc"))
    await s.stop()


async def test_a_fill_while_we_were_down_counts_once(tmp_path, nifty_fut):
    working = await _day_with_a_working_order(tmp_path, nifty_fut)
    broker = FakeBroker()
    broker.broker_orders = [  # it filled while the server was down
        OrderUpdate(working.client_order_id, working.broker_order_id, OrderStatus.FILLED,
                    150, 2400000, last_fill_qty=150, last_fill_price_paise=2400000)
    ]  # fmt: skip
    broker.broker_positions = [BrokerPosition(FUT, 150, 2400000)]
    s = Server(tmp_path, nifty_fut, broker=broker)
    await s.ready()
    assert s.runtime.positions.net_qty(FUT) == 150
    assert s.runtime.orders.get(working.client_order_id).status is OrderStatus.FILLED
    await s.stop()


async def test_a_position_difference_keeps_trading_shut(tmp_path, nifty_fut):
    broker = FakeBroker()
    broker.broker_positions = [BrokerPosition(FUT, 75, ASK)]  # a trade made outside MockTrade
    s = Server(tmp_path, nifty_fut, broker=broker)
    gate = await s.ready("failed")
    assert f"{FUT}: MockTrade rebuilt +0, the broker shows +75" in gate.reason
    await s.stop()


async def test_kite_carried_positions_are_seeded_at_the_previous_close(tmp_path, nifty_fut):
    broker = FakeBroker()
    broker.broker_positions = [
        BrokerPosition(FUT, 75, 2480000, overnight_qty=75, close_paise=2490000,
                       product=Product.NRML)
    ]  # fmt: skip
    s = Server(tmp_path, nifty_fut, broker=broker)
    await s.ready()
    (p,) = s.runtime.positions.positions()
    assert (p.qty, p.avg_price_paise, p.buy_qty) == (75, 2490000, 0)
    await s.stop()


async def test_algo_leftovers_are_cancelled_only_after_the_broker_has_answered(tmp_path, nifty_fut):
    broker = FakeBroker()
    s = Server(tmp_path, nifty_fut, broker=broker, recover=False)
    child = await s.runtime.orders.submit(
        OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT, Product.NRML,
                     price_paise=2400000, parent_id="A1")
    )  # fmt: skip
    await s.stop()
    broker = FakeBroker()
    broker.broker_orders = [OrderUpdate(child.client_order_id, child.broker_order_id,
                                        OrderStatus.OPEN)]  # fmt: skip
    real = broker.positions
    broker.positions = _failing
    s = Server(tmp_path, nifty_fut, broker=broker)
    await asyncio.sleep(0.2)
    assert broker.cancelled == []  # nothing pulled before the broker has said what's working
    s.task.cancel()
    await asyncio.gather(s.task, return_exceptions=True)
    broker.positions = real
    s.task = asyncio.create_task(s.platform.run(reconcile=False))
    await s.ready()
    async with asyncio.timeout(2):
        while child.broker_order_id not in broker.cancelled:
            await asyncio.sleep(0.01)
    await s.stop()


def test_the_trading_day_starts_at_midnight_in_india():
    start = session_start(datetime(2026, 10, 14, 20, 0, tzinfo=UTC))  # 01:30 on the 15th, IST
    assert start.isoformat() == "2026-10-15T00:00:00+05:30"


def test_recovery_needs_the_audit_trail(nifty_fut):
    store = InstrumentStore()
    platform = Platform(store, MarketData(), KillSwitch("firm"))
    with pytest.raises(ValueError, match="needs the event log"):
        platform.add_account(ACCOUNT, FakeBroker(), ids=ClientIds("MT"), recover_on_start=True)
