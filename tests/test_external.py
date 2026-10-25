"""Trades made outside Laddrix (#355, part of #276).

The failure before: a trade made in the broker's own app moved the broker's position but not
ours. Reconciling raised an alert, and nothing could resolve it: the only ways out were to
wait, or to restart and hope.

    preview ─► the broker's trades in that position that no order of ours placed
            ─► they explain the difference exactly? then they can be adopted
    adopt   ─► risk or admin, with words saying what was checked; each trade at its own
               price, audited with its trade id, never twice; the alert clears
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from laddrix.broker.base import BrokerPosition
from laddrix.broker.fake import FakeBroker
from laddrix.domain.orders import Product, Side
from laddrix.domain.portfolio import Trade
from laddrix.positions.book import PositionBook
from laddrix.positions.external import ExternalError, ExternalTrades

FUT = "NFO:NIFTY26OCTFUT"
T = datetime(2026, 10, 23, 5, 0, tzinfo=UTC)


@dataclass
class TradingBroker(FakeBroker):
    """A fake broker with a tradebook."""

    broker_trades: list[Trade] = field(default_factory=list)

    async def trades(self) -> list[Trade]:
        return list(self.broker_trades)


def outside(tid, side, qty, price, client_order_id=""):
    return Trade(tid, f"B{tid}", FUT, side, Product.NRML, qty, price, T, client_order_id)


def held(qty):
    return [BrokerPosition(FUT, qty, 2501000, product=Product.NRML)]


@pytest.fixture
def desk():
    book, broker, notes = PositionBook("acc"), TradingBroker(), []
    tool = ExternalTrades(
        "acc", book, broker, note=lambda k, d, x: notes.append((k, d, x)),
        adopted_ids=lambda: {x["trade_id"] for k, _, x in notes if k == "EXTERNAL_ADOPTED"},
    )  # fmt: skip
    return book, broker, notes, tool


async def test_trades_that_explain_the_difference_are_adopted_at_their_own_prices(desk):
    book, broker, notes, tool = desk
    broker.broker_positions = held(75)
    broker.broker_trades = [
        outside("T1", Side.BUY, 50, 2501000),
        outside("T2", Side.BUY, 25, 2502000),
    ]
    preview = await tool.preview(FUT, Product.NRML)
    assert preview.explains and (preview.ours, preview.broker) == (0, 75)
    assert book.positions() == []  # a preview changes nothing
    done = await tool.adopt(
        FUT, Product.NRML, "risk", "checked the Kite order book: Ravi's phone order"
    )
    assert len(done.trades) == 2
    (p,) = book.positions()
    assert (p.qty, p.buy_value_paise) == (75, 50 * 2501000 + 25 * 2502000)  # the true cost
    assert [x["trade_id"] for k, _, x in notes] == ["T1", "T2"]
    assert all(
        x["by"] == "risk" and "Ravi's phone order" in x["acknowledgement"] for _, _, x in notes
    )
    again = await tool.preview(FUT, Product.NRML)
    assert not again.explains and "agree (+75): nothing to adopt" in again.words
    with pytest.raises(ExternalError):  # never twice
        await tool.adopt(FUT, Product.NRML, "risk", "checked the Kite order book again")


async def test_trades_that_dont_explain_it_are_never_adopted(desk):
    book, broker, notes, tool = desk
    broker.broker_positions = held(150)
    broker.broker_trades = [outside("T1", Side.BUY, 75, 2501000)]
    preview = await tool.preview(FUT, Product.NRML)
    assert not preview.explains
    assert (
        "a difference of +150; the broker's trades made outside Laddrix come to +75"
        in preview.words
    )
    with pytest.raises(ExternalError, match="don't explain it"):
        await tool.adopt(FUT, Product.NRML, "risk", "checked the Kite order book")
    assert book.positions() == [] and notes == []


async def test_our_own_trades_and_repeated_rows_dont_count(desk):
    book, broker, _, tool = desk
    book.apply_fill(FUT, Product.NRML, Side.BUY, 75, 2501000)  # ours, through Laddrix
    broker.broker_positions = held(100)
    broker.broker_trades = [
        outside("T0", Side.BUY, 75, 2501000, client_order_id="MT1"),  # ours: not outside
        outside("T1", Side.SELL, 50, 2501000),
        outside("T2", Side.BUY, 75, 2500000),
        outside("T2", Side.BUY, 75, 2500000),  # the same trade listed twice
    ]
    preview = await tool.preview(FUT, Product.NRML)
    assert preview.explains and [t.trade_id for t in preview.trades] == ["T1", "T2"]


async def test_a_reason_is_required(desk):
    _, broker, _, tool = desk
    broker.broker_positions = held(75)
    broker.broker_trades = [outside("T1", Side.BUY, 75, 2501000)]
    with pytest.raises(ExternalError, match="say what you checked"):
        await tool.adopt(FUT, Product.NRML, "risk", "ok")


async def test_adopted_trades_come_back_after_a_restart(tmp_path, nifty_fut):
    from laddrix.domain.accounts import BrokerAccount
    from laddrix.marketdata.service import MarketData
    from laddrix.orders.model import ClientIds
    from laddrix.platform import Platform
    from laddrix.refdata.store import InstrumentStore
    from laddrix.risk.kill_switch import KillSwitch
    from laddrix.store.events import EventLog

    store = InstrumentStore()
    store.add(nifty_fut)
    broker = TradingBroker(broker_positions=held(75),
                           broker_trades=[outside("T1", Side.BUY, 75, 2501000)])  # fmt: skip

    def start(recover):
        log = EventLog(tmp_path / "events.db")
        log.start()
        platform = Platform(store, MarketData(), KillSwitch("firm"), log)
        rt = platform.add_account(BrokerAccount("acc", "kite", "AB1", "ayush"), broker,
                                  ids=ClientIds("MT"), recover_on_start=recover)  # fmt: skip
        return log, platform, rt

    log, platform, rt = start(False)
    await rt.external.adopt(FUT, Product.NRML, "risk", "checked the Kite order book")
    assert rt.reconciler.last.ok  # the alert is gone: both say +75
    await log.aclose()
    log, platform, rt = start(True)
    try:
        await platform._recover(rt, retry_s=0.01)
        assert rt.recovery.state == "ok", rt.recovery.reason
        assert [(p.instrument_id, p.qty) for p in rt.positions.positions()] == [(FUT, 75)]
        assert await rt.external.preview(FUT, Product.NRML) is not None
        assert not (await rt.external.preview(FUT, Product.NRML)).explains  # adopted already
    finally:
        await log.aclose()


async def test_only_risk_or_admin_may_resolve(tmp_path, nifty_fut):
    from test_server import World

    w = World(tmp_path, nifty_fut)
    riya = await w.logged_in("riya")
    url = "/api/accounts/acc-riya/external-trades"
    r = await riya.get(url, params={"instrument_id": FUT, "product": "NRML"})
    assert r.status_code == 403
    risk = await w.logged_in("risk")
    r = await risk.get(url, params={"instrument_id": FUT, "product": "NRML"})
    assert r.status_code == 409  # this fake broker gives no trade list: nothing to resolve from
    await riya.aclose()
    await risk.aclose()
    w.log.close()


async def test_a_later_outside_trade_is_adopted_alone_even_before_the_audit_is_on_disk():
    """T1 adopted; then T2 is made in the phone app. Only T2 explains the new difference: T1
    must not count again, even while the audit trail hasn't written T1 yet."""
    book, broker, notes = PositionBook("acc"), TradingBroker(), []
    # adopted_ids=set: the audit trail is behind, it knows of nothing adopted yet
    tool = ExternalTrades("acc", book, broker, note=lambda k, d, x: notes.append(x),
                          adopted_ids=set)  # fmt: skip
    broker.broker_positions = held(75)
    broker.broker_trades = [outside("T1", Side.BUY, 75, 2501000)]
    await tool.adopt(FUT, Product.NRML, "risk", "checked the Kite order book")
    broker.broker_positions = held(100)
    broker.broker_trades.append(outside("T2", Side.BUY, 25, 2503000))
    done = await tool.adopt(FUT, Product.NRML, "risk", "checked the Kite order book again")
    assert [t.trade_id for t in done.trades] == ["T2"]
    assert book.positions()[0].qty == 100
