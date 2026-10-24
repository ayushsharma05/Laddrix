"""Data that says what it is, and a bad morning that leaves no unintended exposure (#277).

time & sales   every field an estimate, said so; an epoch per server run, so a screen
               starts its tape again after a restart instead of waiting for old numbers
movers         names what it ranked: only contracts with a live price here
reports        say how fresh their marks are ("prices as of")
backtests      a candle-based run says its fills are an estimate, not a guarantee
the drill      a lost answer, a full disk, a quiet feed and a crash, one after another:
               nothing placed twice, nothing placed while the disk failed, nothing added
               on a stale price, and after the restart the position is exactly the broker's
"""

import time
from datetime import UTC, date, datetime

from test_recovery import FUT, Server, request

from mocktrade.broker.base import BrokerPosition
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import OrderStatus, OrderUpdate
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.movers import Movers
from mocktrade.marketdata.prints import TimeAndSales
from mocktrade.reports.daily import build_day, report_to_dict
from mocktrade.research.backtest import CANDLE_NOTE, BacktestSpec, Run


def test_each_server_run_numbers_its_tape_under_its_own_epoch():
    first, second = TimeAndSales(), TimeAndSales()
    for tape in (first, second):
        tape.on_tick(Tick(FUT, 2500000, volume=100))
        tape.on_tick(Tick(FUT, 2500100, volume=175))
        assert [p.seq for p in tape.recent(FUT)] == [1]  # both start at 1 ...
    assert first.epoch != second.epoch and len(first.epoch) == 8  # ... under different names


async def test_the_api_says_what_is_estimated_and_which_run_it_is(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN))
    c = await w.logged_in()
    tape = (await c.get(f"/api/instruments/{FUT}/prints")).json()
    assert len(tape["epoch"]) == 8
    for words in ("the quantity is the volume traded between two snapshots", "the time is",
                  "the side is inferred"):  # fmt: skip
        assert words in tape["estimate"]
    movers = (await c.get("/api/market/movers", params={"by": "volume"})).json()
    assert movers["universe"] == (f"ranked among the {movers['of']} contracts with a live price "
                                  "on this server (those a screen, an order or an algo "
                                  "watches), not the whole exchange")  # fmt: skip
    await c.aclose()
    w.log.close()


def test_movers_name_their_universe(nifty_fut):
    latest = [Tick(FUT, 2500000, close_paise=2490000, volume=75)]
    answer = Movers(lambda: latest, {FUT: nifty_fut}.get).top("gainers")
    assert (
        answer["of"] == 1 and "1 contracts with a live price on this server" in answer["universe"]
    )


async def test_a_report_says_how_fresh_its_marks_are(tmp_path, nifty_fut):
    from test_reports import MODEL, fill
    from test_reports import instruments as lookup

    from mocktrade.domain.orders import Side
    from mocktrade.store.events import EventLog

    log = EventLog(tmp_path / "events.db")
    log.start()
    day = date(2026, 10, 12)
    fill(log, "M1", Side.BUY, 75, 25000, datetime(2026, 10, 12, 4, 0, tzinfo=UTC))
    await log.flush()
    marked = datetime(2026, 10, 12, 9, 59, 58, tzinfo=UTC)

    def prices(i):
        return Tick(i, 2505000, received_at=marked) if i == FUT else None

    later = datetime(2026, 10, 12, 10, 30, tzinfo=UTC)
    r = report_to_dict(build_day("acc", day, log, lookup(nifty_fut), MODEL, prices=prices,
                                 now=later, final=True))  # fmt: skip
    assert r["positions"][0]["mark_at"] == marked.isoformat()
    assert r["prices_as_of"] == marked.isoformat()
    unpriced = report_to_dict(build_day("acc", day, log, lookup(nifty_fut), MODEL, prices=None,
                                        now=marked, final=False))  # fmt: skip
    assert unpriced["prices_as_of"] is None and unpriced["positions"][0]["mark_at"] is None
    await log.aclose()


def test_a_candle_based_run_says_its_fills_are_an_estimate():
    spec = BacktestSpec(("2026-10-16",), "SLICER", FUT, "BUY", 75)
    candles = Run("BT1", spec, candles=True).to_dict(full=False)
    assert candles["fills_note"] == CANDLE_NOTE
    assert "not what traded" in CANDLE_NOTE and "better or worse" in CANDLE_NOTE
    assert "fills_note" not in Run("BT2", spec, candles=False).to_dict(full=False)


class Exchange(FakeBroker):
    """What reached the exchange, even when its answer never came back."""

    lose_answer = False

    async def place(self, request):
        broker_order_id = await super().place(request)
        if self.lose_answer:
            raise TimeoutError("read timed out")  # it is at the exchange; we never heard
        return broker_order_id


async def test_a_bad_morning_leaves_no_unintended_exposure(tmp_path, nifty_fut):
    exchange = Exchange()
    first = Server(tmp_path, nifty_fut, exchange, recover=False)
    orders = first.runtime.orders
    orders._follow_up_s = ()  # nobody asks before the crash

    # 1. an order that works normally: resting at the exchange
    resting = await orders.submit(request(qty=75, price=2400000))
    assert resting.status is OrderStatus.OPEN or resting.broker_order_id

    # 2. the answer is lost: at the exchange, unknown here; its risk is held, never re-sent
    exchange.lose_answer = True
    lost = await orders.submit(request(qty=75, price=2400010))  # not a double click
    exchange.lose_answer = False
    assert lost.pending.value == "UNCONFIRMED", (lost.status, lost.reason)

    # 3. the disk fills up: nothing new is sent
    first.log.healthy, first.log.last_error = False, OSError(28, "No space left on device")
    refused = await orders.submit(request(qty=75, price=2400020))
    assert (
        refused.status is OrderStatus.REJECTED and "couldn't be recorded on disk" in refused.reason
    )
    first.log.healthy, first.log.last_error = True, None

    # 4. the feed goes quiet: nothing that adds risk goes on a two-minute-old price
    real = first.md._clock
    first.md._clock = lambda: real() + 120
    stale = await orders.submit(request(qty=75, price=2400030))
    assert stale.status is OrderStatus.REJECTED and "s old (limit 60 s)" in stale.reason
    first.md._clock = real

    # 5. the server crashes
    assert len(exchange.placed) == 2  # the resting order and the lost one: nothing else
    await first.log.flush()
    await first.stop()

    # the exchange, meanwhile: the lost order filled; the resting one still rests
    exchange.broker_orders = [
        OrderUpdate(resting.client_order_id, resting.broker_order_id, OrderStatus.OPEN),
        OrderUpdate(lost.client_order_id, "F2", OrderStatus.FILLED, 75, 2400010,
                    last_fill_qty=75, last_fill_price_paise=2400010),
    ]  # fmt: skip
    exchange.broker_positions = [BrokerPosition(FUT, 75, 2400010)]
    again = Server(tmp_path, nifty_fut, exchange)
    await again.ready()  # rebuilt and checked against the exchange: they agree
    assert again.runtime.positions.net_qty(FUT) == 75  # exactly the exchange's: no more
    assert again.runtime.orders.get(lost.client_order_id).status is OrderStatus.FILLED
    assert again.runtime.orders.get(resting.client_order_id).status is OrderStatus.OPEN
    assert len(exchange.placed) == 2  # nothing was placed twice, before or after the restart
    started = time.monotonic()
    await again.runtime.orders.aclose()
    await again.stop()
    assert time.monotonic() - started < 5
