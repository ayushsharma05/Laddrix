"""STOP, cancel and flatten as separate safe operations (#262).

The failure before: a STOP (or the daily-loss stop) blocked every new order, exits too, so
closing a position meant resuming trading first; and a STOP during an unanswered order turned
it into "cancel pending", so the evidence could never settle it. Now STOP says exactly what it
did, and Flatten closes positions while trading stays stopped, only through the exit desk,
only reduce-only, never a position whose size isn't known."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_mtm import Clock, flat, move
from test_portfolio import FUT, Paper
from test_unconfirmed import (
    listed,
    world,  # noqa: F401  (a fixture: a broker that never answers)
)
from test_unconfirmed import req as unanswered_request

from mocktrade.algos.builtin import BUILTIN
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.orders.codec import request_from_dict, request_to_dict
from mocktrade.orders.model import Pending
from mocktrade.positions.reconcile import Mismatch
from mocktrade.risk.closing import CloseResult
from mocktrade.risk.flatten import Flatten
from mocktrade.risk.kill_switch import stop_trading
from mocktrade.risk.loss_watch import DailyLossWatch

S = OrderStatus


@pytest.fixture
async def day(nifty_fut, tmp_path):
    clock = Clock()
    p = Paper(nifty_fut, tmp_path, clock=clock, algo_types=BUILTIN)
    p.clock = clock
    yield p
    await p.stop()


def order(side=Side.SELL, qty=75, *, reduce_only=False, forced_close=False):
    return OrderRequest("", "acc", "ayush", FUT, side, qty, OrderType.MARKET, Product.MIS,
                        market_protection=Decimal(-1), reduce_only=reduce_only,
                        forced_close=forced_close)  # fmt: skip


async def stop(p, reason="manual stop"):
    before = p.rt.orders.working()
    live = sum(not a.state.is_final for a in p.rt.algos.algos())
    failed = await stop_trading(p.rt.switch, [p.rt.orders], reason, by="ayush")
    return p.rt.flatten.stop_report(before, failed.get("acc", []), live, "ayush", reason)


# --- the kill switch: blocks everything but a forced close that only reduces -------------------


async def test_under_stop_only_a_forced_reducing_close_passes(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await stop(p)
    for request in (order(), order(reduce_only=True), order(forced_close=True),
                    order(Side.BUY, forced_close=True)):  # fmt: skip
        refused = await p.rt.orders.submit(request)
        assert refused.status is S.REJECTED and "trading is stopped" in refused.reason
    reverse = await p.rt.orders.submit(order(qty=225, reduce_only=True, forced_close=True))
    assert reverse.reason.startswith("would close more than is open")  # still every check
    closing = await p.rt.orders.submit(order(qty=150, reduce_only=True, forced_close=True))
    assert closing.status is not S.REJECTED
    await flat(p, product=Product.MIS)
    assert p.rt.switch.engaged  # trading is still stopped


def test_forced_close_is_saved_with_the_order_and_absent_means_no():
    assert request_from_dict(request_to_dict(order(forced_close=True))).forced_close is True
    old = request_to_dict(order())
    old.pop("forced_close")
    assert request_from_dict(old).forced_close is False


# --- flatten -------------------------------------------------------------------------------------


async def test_flatten_closes_while_stopped_without_resuming(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2501010, Product.NRML)
    target = await p.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.SELL, 75,
                                                   OrderType.LIMIT, Product.NRML,
                                                   price_paise=2510000))  # fmt: skip
    algo = p.rt.algos.start("TRAILING_STOP", user_id="ayush", instrument_id=FUT,
                            side=Side.SELL, qty=150, params={"trail_paise": 2000},
                            product=Product.MIS)  # fmt: skip
    report = await stop(p, "feeling unsure")
    assert report["lines"] == [
        "stopped by ayush: feeling unsure",
        f"1 working order asked to cancel ({target.client_order_id})",
        "1 algo stopped",
        "positions kept: NIFTY26OCTFUT MIS +150, NIFTY26OCTFUT NRML +75; use Flatten to close them",
    ]
    status = p.rt.flatten.status()
    assert status["stopped"] and status["stop"]["reason"] == "feeling unsure"
    assert status["stop_report"] == report and status["can_flatten"]
    assert [(r["product"], r["qty"], r["unknown"]) for r in status["positions"]] == [
        ("MIS", 150, None), ("NRML", 75, None)]  # fmt: skip
    assert status["explain"][0].endswith("It does not close positions.")
    done = await p.rt.flatten.run("ayush")
    await flat(p)
    await asyncio.sleep(0.1)
    assert p.rt.positions.net_qty(FUT) == 0 and p.rt.switch.engaged
    sent = [p.rt.orders.get(c) for r in done["positions"] for c in r["orders"]]
    assert all(o.request.reduce_only and o.request.forced_close for o in sent)
    assert sorted(o.request.qty for o in sent) == [75, 150]
    assert done["stopped"] is True and p.rt.flatten.status()["last"] == done
    assert p.rt.algos.get(algo.algo_id).state.is_final
    await p.log.flush()
    (audit,) = [e.detail for e in p.log.events(account_id="acc") if e.kind == "FLATTEN"]
    assert audit.startswith("flatten by ayush: closing +150 NFO:NIFTY26OCTFUT MIS: sent ")


async def test_repeated_clicks_close_once(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await stop(p)
    first, second = await asyncio.gather(p.rt.flatten.run("ayush"), p.rt.flatten.run("ayush"))
    await flat(p)
    await asyncio.sleep(0.1)
    assert p.rt.positions.net_qty(FUT) == 0
    sold = [o for o in p.rt.orders.orders() if o.request.forced_close]
    assert [o.request.qty for o in sold] == [150]  # one close only
    assert len(first["positions"]) == 1
    assert second["positions"] == [] or not second["positions"][0]["orders"]


async def test_a_position_whose_size_is_not_known_is_never_flattened(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2501010, Product.NRML)
    mismatch = Mismatch(FUT, 150, 225, datetime.now(UTC), Product.MIS)
    p.rt.reconciler._alerts[mismatch.key] = mismatch  # the broker disagrees on MIS
    rows = {r["product"]: r for r in p.rt.flatten.status()["positions"]}
    assert rows["MIS"]["unknown"] == mismatch.message and rows["NRML"]["unknown"] is None
    done = await p.rt.flatten.run("ayush")
    by = {r["product"]: r for r in done["positions"]}
    assert by["MIS"]["orders"] == [] and by["MIS"]["left_open"] == 150
    assert by["MIS"]["summary"] == (f"not closed: {FUT} MIS +150: the size isn't known "
                                    f"({mismatch.message}); close it at the broker")  # fmt: skip
    assert by["NRML"]["closed"]
    await flat(p, product=Product.NRML)
    assert p.rt.positions.net_qty(FUT) == 150


async def test_a_difference_being_rechecked_is_also_unknown(day):
    from mocktrade.positions.reconcile import CheckResult

    p = day
    await p.buy(FUT, 75, 2501010, Product.MIS)
    p.rt.reconciler.last = CheckResult(False, datetime.now(UTC), (), ((FUT, None),))
    (row,) = p.rt.flatten.status()["positions"]
    assert row["unknown"] == "the broker's position differs and is being re-checked"
    assert p.rt.flatten.status()["can_flatten"] is False


async def test_flatten_one_instrument_and_product_only(day):
    p = day
    await p.buy(FUT, 75, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2501010, Product.NRML)
    done = await p.rt.flatten.run("ayush", instrument_id=FUT, product=Product.NRML)
    assert [r["product"] for r in done["positions"]] == ["NRML"]
    await flat(p, product=Product.NRML)
    assert p.rt.positions.net_qty(FUT) == 75
    nothing = await p.rt.flatten.run("ayush", instrument_id="NSE:RELIANCE")
    assert nothing["summary"] == "nothing open to close"


async def test_after_the_daily_loss_stop_flatten_works_without_a_reset(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await move(p, 2486000)  # 150 × −150.10 = −₹22,515: past the ₹20,000 daily limit
    watch = DailyLossWatch(p.rt.positions.day_pnl_paise, 20_000_00, p.rt.switch, [p.rt.orders])
    assert watch.check()  # what the server's own watch does on this loss
    await watch.settle()
    assert p.rt.switch.engaged and "daily loss limit" in p.rt.switch.state.reason
    plain = await p.rt.orders.submit(order(qty=150))
    assert plain.status is S.REJECTED  # an ordinary close still needs a reset
    await p.rt.flatten.run("ayush")
    await flat(p)
    assert p.rt.switch.engaged


async def test_refused_and_partial_closes_stay_visible(nifty_fut):
    class Desk:
        async def close(self, instrument, product, **_):
            from test_exits import _View, sell

            refused = _View("X1", sell(75), 0, status=S.REJECTED)
            refused.reason = "price band"  # type: ignore[attr-defined]
            return CloseResult(instrument.id, product, position=150, sent=[refused],
                               left_open=75)  # fmt: skip

    class Book:
        def positions(self):
            from types import SimpleNamespace

            return [SimpleNamespace(instrument_id=FUT, product=Product.MIS, qty=150)]

    class Orders:
        def working(self):
            return []

    f = Flatten("acc", Book(), Orders(), Desk(), {FUT: nifty_fut}.get, max_lots=10)
    (row,) = (await f.run("ayush"))["positions"]
    assert row["refused"] == ["price band"] and row["left_open"] == 75
    assert row["summary"].endswith("75 left to orders already closing it; refused: price band")


# --- STOP and an order the broker never answered ----------------------------------------------


async def test_stop_keeps_an_unanswered_order_unconfirmed_and_says_so(world):  # noqa: F811
    view = await world.om.submit(unanswered_request())
    await world.om.cancel(view.client_order_id)  # what STOP's cancel-all does
    still = world.om.get(view.client_order_id)
    assert still.pending is Pending.UNCONFIRMED  # not "cancel pending": evidence still works
    world.om.reconcile([listed(view, broker_id="K1")])  # the broker had it after all
    await world.om.settle()
    assert world.broker.cancelled == ["K1"]  # so the asked-for cancel goes out at once
    await world.om.aclose()


async def test_a_stopped_unanswered_order_the_broker_never_had_is_still_rejected(world):  # noqa: F811
    from mocktrade.orders.manager import UNSEEN_AFTER, UNSEEN_CHECKS

    view = await world.om.submit(unanswered_request())
    await world.om.cancel(view.client_order_id)
    for _ in range(UNSEEN_CHECKS):
        world.clock.advance(UNSEEN_AFTER.total_seconds())
        world.om.reconcile([])
    gone = world.om.get(view.client_order_id)
    assert gone.status is S.REJECTED and "never received this order" in gone.reason
    await world.om.aclose()


def test_the_stop_report_names_failures_and_unanswered_orders(nifty_fut):
    from test_exits import _View, sell

    class Book:
        def positions(self):
            return []

    f = Flatten("acc", Book(), None, None, {}.get, max_lots=1)
    before = [_View("W1", sell(75), 75), _View("W2", sell(75), 75),
              _View("U1", sell(75), 75, Pending.UNCONFIRMED)]  # fmt: skip
    report = f.stop_report(before, [("W2", "already filled")], 0, "risk", "exchange issue")
    assert report["lines"] == [
        "stopped by risk: exchange issue",
        "1 working order asked to cancel (W1)",
        "couldn't cancel W2: already filled; check it at the broker",
        "U1: no answer from the broker yet; cancelled the moment the broker names it, else "
        "settled by its order list",
        "0 algos stopped",
        "no open positions",
    ]
    assert report["failed"] == [{"client_order_id": "W2", "reason": "already filled"}]


async def test_one_flatten_at_a_time_and_the_screen_sees_it_running(nifty_fut):
    from types import SimpleNamespace

    release, calls = asyncio.Event(), []

    class Desk:
        async def close(self, instrument, product, *, user_id, **_):
            calls.append(user_id)
            await release.wait()
            return CloseResult(instrument.id, product, position=75)

    class Book:
        def positions(self):
            return [SimpleNamespace(instrument_id=FUT, product=Product.MIS, qty=75)]

    class Orders:
        def working(self):
            return []

    f = Flatten("acc", Book(), Orders(), Desk(), {FUT: nifty_fut}.get, max_lots=10)
    first = asyncio.create_task(f.run("ayush"))
    second = asyncio.create_task(f.run("risk"))
    await asyncio.sleep(0.01)
    assert f.status()["running"] is True and calls == ["ayush"]  # the second waits its turn
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["ayush", "risk"] and f.status()["running"] is False
