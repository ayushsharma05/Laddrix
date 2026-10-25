"""Prices that act on orders: every tick, never stale, never out of order (#267).

The failures before: algos and the paper broker read prices conflated (newest wins), so a
spike through a stop that came back before they read was never seen; a late tick could
overwrite newer state; and the risk check only asked for a positive price, so a quiet
instrument's old price was used while other instruments ticked busily."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_protect import FUT, tick
from test_spreader import NOV, OCT, quote

from laddrix.algos.builtin import BUILTIN
from laddrix.algos.engine import AlgoEngine
from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import OrderRequest, OrderType, Product, Side
from laddrix.marketdata.service import MarketData
from laddrix.ops.alerts import Alerts
from laddrix.ops.checks import PriceOverload
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.checks import RiskGate
from laddrix.risk.kill_switch import KillSwitch
from laddrix.risk.limits import RiskLimits

T0 = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)


class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# --- every tick for what acts on prices -----------------------------------------------------


async def test_a_spike_through_a_stop_that_retraces_before_the_algo_reads_still_exits(nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(tick(2501000))
    broker = FakeBroker()
    om = OrderManager("acc", broker, store.get, ids=ClientIds("MT"))
    engine = AlgoEngine("acc", om, md, store.get, BUILTIN)
    engine.start("TRAILING_STOP", user_id="ayush", instrument_id=FUT, side=Side.SELL, qty=75,
                 params={"trail_paise": 2000})  # stop at 24,990.00  # fmt: skip
    runner = asyncio.create_task(engine.run())
    await asyncio.sleep(0.01)
    md.on_tick(tick(2498000))  # through the stop…
    md.on_tick(tick(2502000))  # …and back, before the engine had a chance to read
    await asyncio.sleep(0.05)
    await engine.settle()
    runner.cancel()
    assert [(r.side, r.qty) for r in broker.placed] == [(Side.SELL, 75)]  # touched: it exits


def test_every_tick_readers_get_each_tick_in_order_screens_get_the_newest():
    md = MarketData()
    screens, algos = md.subscribe([FUT]), md.subscribe([FUT], every_tick=True)
    for p in (100, 90, 110):
        md.on_tick(tick(p * 1000))
    assert [t.ltp_paise for t in screens.drain()] == [110000]
    assert [t.ltp_paise for t in algos.drain()] == [100000, 90000, 110000]


def test_a_reader_that_falls_behind_is_bounded_and_the_overload_shows():
    md = MarketData()
    slow = md.subscribe([FUT], every_tick=True)
    slow.limit = 3
    alerts = Alerts()
    overload = PriceOverload(alerts, md)
    for p in range(1, 6):
        md.on_tick(tick(p * 1000))
    assert [t.ltp_paise for t in slow.drain()] == [3000, 4000, 5000]  # the oldest went
    assert (slow.dropped, md.dropped) == (2, 2)
    overload.check()
    (band,) = alerts.active()
    assert band.key == "price_overload" and "2 ticks were dropped" in band.text
    overload.check()
    assert alerts.active() == []  # no new drops: cleared


# --- late ticks -----------------------------------------------------------------------------


def test_a_late_tick_never_replaces_newer_state():
    md = MarketData()
    md.on_tick(tick(2501000)._replace(exchange_time=T0 + timedelta(seconds=2)))
    watcher = md.subscribe([FUT], every_tick=True)
    watcher.drain()
    md.on_tick(tick(2400000)._replace(exchange_time=T0 + timedelta(seconds=1)))  # late
    assert md.latest(FUT).ltp_paise == 2501000 and md.late_ticks == 1
    assert watcher.drain() == []  # nobody acts on it
    md.on_tick(tick(2502000)._replace(exchange_time=T0 + timedelta(seconds=3)))
    assert md.latest(FUT).ltp_paise == 2502000


# --- a busy instrument can't hide a quiet one ---------------------------------------------


@pytest.fixture
def gate(nifty_fut):
    from dataclasses import replace
    from datetime import date

    store = InstrumentStore()
    store.add(nifty_fut)
    store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
    mono = Mono()
    md = MarketData(clock=mono)
    held = {FUT: 0}
    risk = RiskGate(RiskLimits(), instruments=store.get, prices=md.latest,
                    position=lambda i: held.get(i, 0), working=lambda: (),
                    day_pnl_paise=lambda: 0, switches=(KillSwitch("firm"),),
                    product_position=lambda i, p: held.get(i, 0),
                    price_age=md.age_s)  # fmt: skip

    class G:
        pass

    g = G()
    g.md, g.mono, g.risk, g.held = md, mono, risk, held
    return g


def order(instrument=FUT, side=Side.BUY, qty=75, **kw):
    return OrderRequest("MT1", "acc", "ayush", instrument, side, qty, OrderType.LIMIT,
                        Product.NRML, price_paise=2501000, **kw)  # fmt: skip


async def test_a_quiet_instrument_is_stale_however_busy_another_one_is(gate):
    gate.md.on_tick(tick(2501000))  # FUT, then it goes quiet
    for _ in range(90):  # NOV ticks busily for 90 s
        gate.mono.t += 1
        gate.md.on_tick(tick(2501000)._replace(instrument_id=NOV))
    refused = await gate.risk.admit_new(order())
    assert refused == ("the last price for NIFTY26OCTFUT is 90 s old (limit 60 s): open its "
                       "ladder or wait for a new price; closing trades still go")  # fmt: skip
    assert await gate.risk.admit_new(order(NOV)) is None  # NOV's own price is fresh


async def test_closing_on_a_stale_price_still_goes(gate):
    gate.md.on_tick(tick(2501000))
    gate.held[FUT] = 75
    gate.mono.t += 300
    assert await gate.risk.admit_new(order(side=Side.SELL)) is None  # it only reduces
    assert await gate.risk.admit_new(order(side=Side.SELL, reduce_only=True)) is None
    assert "s old" in await gate.risk.admit_new(order(side=Side.BUY))


async def test_growing_an_order_on_a_stale_price_is_refused(gate):
    from laddrix.domain.orders import OrderStatus
    from laddrix.orders.model import OrderView, Pending

    gate.md.on_tick(tick(2501000))
    gate.mono.t += 120
    working = OrderView(order(), "B1", OrderStatus.OPEN, 0, 0, "", T0, T0, Pending.NONE)
    assert "s old" in await gate.risk.admit_modify(working, order(qty=150))
    assert await gate.risk.admit_modify(working, order(qty=75)) is None  # not growing


# --- spread legs ---------------------------------------------------------------------------


async def test_a_spread_waits_when_its_leg_prices_are_far_apart_in_time(nifty_fut):
    from dataclasses import replace
    from datetime import date

    store = InstrumentStore()
    store.add(nifty_fut)
    store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
    mono = Mono()
    md = MarketData(clock=mono)
    md.on_tick(quote(NOV, 2514000, 2514010))
    mono.t += 10  # leg B is now 10 s older than leg A
    md.on_tick(quote(OCT, 2501000, 2501010))
    broker = FakeBroker()
    om = OrderManager("acc", broker, store.get, ids=ClientIds("MT"))
    engine = AlgoEngine("acc", om, md, store.get, BUILTIN)
    algo = engine.start("SPREAD", user_id="ayush", instrument_id=OCT, side=Side.BUY, qty=150,
                        params={"leg2": NOV, "level_paise": -13000, "payup_ticks": 2})  # fmt: skip
    await engine.settle()
    assert broker.placed == []
    assert engine.get(algo.algo_id).status == "waiting: leg prices 10 s apart"
    md.on_tick(quote(NOV, 2514000, 2514010))  # leg B fresh again
    engine.on_tick(md.latest(NOV))
    await engine.settle()
    assert [r.instrument_id for r in broker.placed] == [OCT]  # now it quotes


async def test_a_resting_paper_order_fills_on_a_dip_that_retraces_before_it_reads(nifty_fut):
    from laddrix.broker.paper import PaperBroker
    from laddrix.domain.orders import OrderStatus

    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(tick(2501000))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    await paper.place(OrderRequest("MT1", "acc", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT,
                                   Product.NRML, price_paise=2499000))  # fmt: skip
    await asyncio.sleep(0.02)
    md.on_tick(tick(2498000))  # the ask dips to 24,980.10: below the limit…
    md.on_tick(tick(2502000))  # …and is back before the broker read anything
    updates = []
    async with asyncio.timeout(1):
        async for u in paper.updates():
            updates.append(u.status)
            if u.status is OrderStatus.FILLED:
                break
    assert updates[-1] is OrderStatus.FILLED
    await paper.aclose()
