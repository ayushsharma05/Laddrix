"""Algos across a restart (#426): an Autospreader that was working comes back PAUSED with its
children, once the broker has said what happened to every order; nothing is resent blindly.

Each test runs the server, stops it, and starts it again on the same audit trail with a fake
broker standing in for a real one (Kite keeps working orders across our restart; its order
list says what happened while we were down).

Calendar OCT − NOV quoting OCT, bought at −130.00: the quote is OCT 25,010.00 and its hedge
sells NOV at 25,139.80 (bid − 2 ticks).
"""

import asyncio
from dataclasses import replace
from datetime import date

from mocktrade.algos.autospreader import algo_type
from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoState
from mocktrade.broker.base import BrokerPosition
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.accounts import BrokerAccount, User
from mocktrade.domain.orders import OrderStatus, OrderUpdate, Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.spreads.model import SpreadLeg
from mocktrade.spreads.store import SpreadBook
from mocktrade.store.events import EventLog

OCT, NOV = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT"
ACCOUNT = BrokerAccount("acc", "fake", "F1", "ayush")
LIMITS = RiskLimits(max_order_value_paise=10**12)


def quote(instrument_id, bid, ask):
    return Tick(instrument_id, bid, volume=1000, bids=(DepthLevel(bid, 750, 3),),
                asks=(DepthLevel(ask, 750, 3),))  # fmt: skip


class Server:
    """One run of the server with the Autospreader, on the audit trail in `tmp_path`."""

    def __init__(self, tmp_path, nifty_fut, broker, *, ase=True, book=None):
        self.store, self.md = InstrumentStore(), MarketData()
        self.store.add(nifty_fut)
        self.store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
        self.md.on_tick(quote(OCT, 2501000, 2501010))
        self.md.on_tick(quote(NOV, 2514000, 2514010))
        self.log = EventLog(tmp_path / "events.db")
        self.log.start()
        self.book = book or SpreadBook(None, self.store.get)
        self.platform = Platform(self.store, self.md, KillSwitch("firm"), self.log)
        self.broker = broker
        types = (*BUILTIN, algo_type(self.book.get)) if ase else BUILTIN
        self.runtime = self.platform.add_account(
            ACCOUNT, broker, ids=ClientIds("MT", self.log.last_counter("MT")), limits=LIMITS,
            algo_types=types, recover_on_start=True,
        )  # fmt: skip
        self.task = asyncio.create_task(self.platform.run(reconcile=False))

    @property
    def algos(self):
        return self.runtime.algos

    @property
    def orders(self):
        return self.runtime.orders

    async def ready(self):
        async with asyncio.timeout(5):
            while self.runtime.recovery.state not in ("ok", "failed"):
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)  # the algo loop restores, then runs
        await self.algos.settle()

    def calendar(self):
        legs = [SpreadLeg(OCT, 1, 1, quote=True, payup_ticks=2), SpreadLeg(NOV, -1, -1)]
        return self.book.create(User("ayush", "Ayush"), "Oct/Nov", legs)

    def start(self, spread, lots=1):
        return self.algos.start("ASE", user_id="ayush", instrument_id=OCT, side=Side.BUY,
                                qty=75 * lots, params={"spread": spread.spread_id,
                                                       "level_paise": -13000})  # fmt: skip

    def fill(self, child_id, qty, price):
        view = self.orders.get(child_id)
        total = view.filled_qty + qty
        status = OrderStatus.FILLED if total >= view.request.qty else OrderStatus.PARTIAL
        self.orders.apply(OrderUpdate(child_id, view.broker_order_id, status, total, price,
                                      last_fill_qty=qty, last_fill_price_paise=price))  # fmt: skip

    def children(self, algo_id, leg=None):
        return [c for c in self.algos.get(algo_id).children
                if leg is None or self.orders.get(c).request.instrument_id == leg]  # fmt: skip

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.log.aclose()


def as_broker_lists(s: Server, filled: dict[str, int] | None = None) -> FakeBroker:
    """A fresh broker that lists every order of the last run as it stood (or as `filled`
    says it filled while we were down), with the matching positions."""
    broker = FakeBroker()
    held: dict[str, int] = {}
    for o in s.orders.orders():
        qty = (filled or {}).get(o.client_order_id, o.filled_qty)
        status = (OrderStatus.FILLED if qty >= o.request.qty
                  else OrderStatus.CANCELLED if o.status is OrderStatus.CANCELLED
                  else OrderStatus.OPEN if not qty else OrderStatus.PARTIAL)  # fmt: skip
        price = o.request.price_paise or 0
        broker.broker_orders.append(OrderUpdate(o.client_order_id, o.broker_order_id, status,
                                                qty, price if qty else 0))  # fmt: skip
        leg = o.request.instrument_id
        held[leg] = held.get(leg, 0) + o.request.side.sign * qty
    broker.broker_positions = [BrokerPosition(i, q, 0) for i, q in held.items() if q]
    return broker


async def legging(tmp_path, nifty_fut):
    """A day that stops while legging: the quote filled, its hedge still working."""
    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    algo = s.start(s.calendar())
    await s.algos.settle()
    (q,) = s.children(algo.algo_id)
    s.fill(q, 75, 2501000)
    await s.algos.settle()
    (hedge,) = s.children(algo.algo_id, NOV)
    return s, algo, q, hedge


async def test_an_ase_comes_back_paused_with_its_working_hedge_and_sends_nothing_twice(
    tmp_path, nifty_fut
):
    s, algo, q, hedge = await legging(tmp_path, nifty_fut)
    broker = as_broker_lists(s)
    book = s.book
    await s.stop()

    again = Server(tmp_path, nifty_fut, broker, book=book)
    await again.ready()
    view = again.algos.get(algo.algo_id)
    assert view is not None and view.state is AlgoState.PAUSED
    assert view.status == "restored after a restart: check it, then resume or stop"
    assert view.children == (q, hedge) and view.extra["unhedged"] == 75
    hedge_view = again.orders.get(hedge)
    assert hedge_view.status is OrderStatus.OPEN  # kept: it reduces risk
    assert hedge_view.broker_order_id not in broker.cancelled
    again.algos.heartbeat()
    await again.algos.settle()
    assert broker.placed == []  # nothing new while paused
    again.algos.resume(algo.algo_id)
    await again.algos.settle()
    assert broker.placed == []  # the working hedge covers the short leg: nothing twice
    again.fill(hedge, 75, 2514000)
    assert again.algos.get(algo.algo_id).state is AlgoState.DONE
    await again.stop()


async def test_a_fill_while_the_server_was_down_is_hedged_on_resume_not_before(tmp_path, nifty_fut):
    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    algo = s.start(s.calendar())
    await s.algos.settle()
    (q,) = s.children(algo.algo_id)
    broker = as_broker_lists(s, filled={q: 75})  # the quote filled while we were down
    book = s.book
    await s.stop()

    again = Server(tmp_path, nifty_fut, broker, book=book)
    await again.ready()
    assert again.algos.get(algo.algo_id).extra["short"] == "75 on NIFTY26NOVFUT"
    again.algos.heartbeat()
    await again.algos.settle()
    assert broker.placed == []  # no hedge before the trader has looked
    again.algos.resume(algo.algo_id)
    await again.algos.settle()
    assert [(r.instrument_id, r.side, r.qty, r.price_paise) for r in broker.placed] == [
        (NOV, Side.SELL, 75, 2513980)]  # fmt: skip
    again.algos.resume(algo.algo_id)  # a second resume: already working, nothing more
    await again.algos.settle()
    assert len(broker.placed) == 1
    await again.stop()


async def test_its_quote_is_cancelled_and_resume_quotes_afresh(tmp_path, nifty_fut):
    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    algo = s.start(s.calendar(), lots=2)
    await s.algos.settle()
    (q,) = s.children(algo.algo_id)
    broker = as_broker_lists(s)
    book = s.book
    await s.stop()

    again = Server(tmp_path, nifty_fut, broker, book=book)
    await again.ready()
    assert again.orders.get(q).broker_order_id in broker.cancelled  # the quote is pulled
    again.orders.apply(OrderUpdate(q, again.orders.get(q).broker_order_id,
                                   OrderStatus.CANCELLED, 0, 0))  # fmt: skip
    again.algos.resume(algo.algo_id)
    await again.algos.settle()
    assert [(r.instrument_id, r.side, r.qty) for r in broker.placed] == [(OCT, Side.BUY, 150)]
    await again.stop()


async def test_an_algo_that_ended_is_not_restored_and_ids_never_repeat(tmp_path, nifty_fut):
    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    algo = s.start(s.calendar())
    await s.algos.settle()
    (q,) = s.children(algo.algo_id)
    s.algos.stop(algo.algo_id)
    await s.algos.settle()
    s.orders.apply(OrderUpdate(q, s.orders.get(q).broker_order_id, OrderStatus.CANCELLED, 0, 0))
    assert s.algos.get(algo.algo_id).state is AlgoState.STOPPED
    broker = as_broker_lists(s)
    book = s.book
    await s.stop()

    again = Server(tmp_path, nifty_fut, broker, book=book)
    await again.ready()
    assert again.algos.get(algo.algo_id) is None
    new = again.start(book.mine(User("ayush", "Ayush"))[0])
    assert new.algo_id != algo.algo_id  # each run's ids carry its start time
    await again.stop()


async def test_it_comes_back_after_every_restart_until_it_ends(tmp_path, nifty_fut):
    s, algo, q, hedge = await legging(tmp_path, nifty_fut)
    broker = as_broker_lists(s)
    book = s.book
    await s.stop()
    for _ in range(2):
        again = Server(tmp_path, nifty_fut, broker, book=book)
        await again.ready()
        assert again.algos.get(algo.algo_id).state is AlgoState.PAUSED
        await again.stop()


async def test_it_works_the_spread_it_started_with_even_if_edited_or_deleted(tmp_path, nifty_fut):
    s, algo, q, hedge = await legging(tmp_path, nifty_fut)
    broker = as_broker_lists(s)
    await s.stop()
    again = Server(tmp_path, nifty_fut, broker)  # a new, empty spread book: the spread is gone
    await again.ready()
    view = again.algos.get(algo.algo_id)
    assert view.state is AlgoState.PAUSED and view.extra["spread_name"] == "Oct/Nov"
    await again.stop()


async def test_one_that_cant_be_rebuilt_is_cancelled_and_noted_in_words(tmp_path, nifty_fut):
    s, algo, q, hedge = await legging(tmp_path, nifty_fut)
    broker = as_broker_lists(s)
    await s.stop()
    again = Server(tmp_path, nifty_fut, broker, ase=False)  # the Autospreader isn't offered
    await again.ready()
    assert again.algos.get(algo.algo_id) is None
    async with asyncio.timeout(2):  # its children are cancelled as orphans
        while again.orders.get(hedge).broker_order_id not in broker.cancelled:
            await asyncio.sleep(0.01)
    await again.log.flush()
    (lost,) = [e for e in again.log.events(account_id="acc") if e.kind == "ALGO_LOST"]
    assert lost.detail == (f"not restored after a restart: ASE BUY 75 {OCT} ({algo.algo_id}): "
                           "no algo called ASE any more; its orders are cancelled")  # fmt: skip
    await again.stop()
    third = Server(tmp_path, nifty_fut, as_broker_lists(again))
    await third.ready()
    await third.log.flush()
    assert [e for e in third.log.events(account_id="acc") if e.kind == "ALGO_LOST"] == [lost]
    await third.stop()  # noted once: lost is an end


async def test_a_start_record_without_its_spread_is_not_rebuilt(tmp_path, nifty_fut):
    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    s.log.record_note("ALGO_STARTED", "acc", "half a record", {
        "algo_id": "Aold-1", "code": "ASE", "family": "spread", "instrument_id": OCT,
        "side": "BUY", "qty": 75, "params": {"spread": "gone", "level_paise": -13000},
        "user_id": "ayush", "product": Product.NRML.value})  # fmt: skip
    s.log.record_note("ALGO_STARTED", "acc", "a broken record", {"algo_id": "Aold-2",
                                                                  "code": "ASE"})  # fmt: skip
    await s.stop()
    again = Server(tmp_path, nifty_fut, FakeBroker())
    await again.ready()
    await again.log.flush()
    lost = [e.detail for e in again.log.events(account_id="acc") if e.kind == "ALGO_LOST"]
    assert lost == [
        f"not restored after a restart: ASE BUY 75 {OCT} (Aold-1): its saved state can't be "
        "read: the spread it was trading was not saved; its orders are cancelled",
        "not restored after a restart: ASE None None None (Aold-2): its record is incomplete "
        "(KeyError('user_id')); its orders are cancelled",
    ]
    await again.stop()
