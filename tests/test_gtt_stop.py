"""GTTs while trading is stopped (#276).

The failure before: STOP cancelled our working orders, but GTTs live at the broker, and the
broker places their orders without asking us. A GTT that could open a position stayed
armed through STOP and could fire minutes later; one that fired was simply adopted.

    STOP ─► every leg only reduces the position ─► kept (a stop-loss still protects)
         ─► any leg could open or add           ─► deleted at the broker (GTT_STOPPED)
         ─► the delete fails                    ─► said (GTT_STOP_UNCLEAR), tried again
         ─► one fires anyway, adding risk       ─► its order cancelled (GTT_FIRED_IN_STOP)
"""

import asyncio

from test_gtt import FUT, LTP, World, single, tick

from laddrix.domain.gtt import GttKind, GttLeg, GttRequest, GttStatus
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side


def opening_buy(trigger=2530000, price=2531000):
    """A buy above the market: with no short to cover, it opens a long when it fires."""
    return GttRequest("acc", "ayush", FUT, Product.NRML, GttKind.SINGLE,
                      (GttLeg(trigger, Side.BUY, 75, price),))  # fmt: skip


async def until(check, timeout=3):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


async def long_75(w):
    await w.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75, OrderType.LIMIT,
                                          Product.NRML, price_paise=LTP + 10))  # fmt: skip
    await until(lambda: w.rt.positions.net_qty(FUT) == 75)


async def running(w):
    return asyncio.create_task(w.platform.run(reconcile=False))


async def stop(run, w):
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    await w.close()


async def test_stop_keeps_stop_losses_and_deletes_gtts_that_could_open(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    run = await running(w)
    try:
        await long_75(w)
        protect = await w.rt.gtt.place(single())  # sell 75 below: the long's stop-loss
        opener = await w.rt.gtt.place(opening_buy())
        w.rt.switch.engage("the desk said stop", "ayush")
        await until(lambda: w.rt.gtt.get(opener.gtt_id).status is GttStatus.DELETED)
        assert w.rt.gtt.get(protect.gtt_id).status is GttStatus.ACTIVE  # still protects
        theirs = {g.gtt_id: g.status for g in await w.broker.gtts()}
        assert theirs[opener.gtt_id] is not GttStatus.ACTIVE  # gone at the broker too
        assert theirs[protect.gtt_id] is GttStatus.ACTIVE
        assert "trading is stopped (the desk said stop)" in w.rt.gtt.get(opener.gtt_id).reason
        await w.log.flush()
        assert "GTT_STOPPED" in w.notes()
    finally:
        await stop(run, w)


async def test_once_the_position_is_closed_its_stop_loss_goes_too(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    run = await running(w)
    try:
        await long_75(w)
        protect = await w.rt.gtt.place(single())
        w.rt.switch.engage("stop", "ayush")
        await asyncio.sleep(0.1)
        assert w.rt.gtt.get(protect.gtt_id).status is GttStatus.ACTIVE
        w.rt.positions.apply_fill(FUT, Product.NRML, Side.SELL, 75, LTP)  # closed (Flatten)
        await until(lambda: w.rt.gtt.get(protect.gtt_id).status is GttStatus.DELETED)
    finally:
        await stop(run, w)


async def test_a_delete_that_fails_is_said_and_tried_again(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    real, calls = w.broker.delete_gtt, []

    async def flaky(gtt_id):
        calls.append(gtt_id)
        if len(calls) == 1:
            raise TimeoutError
        await real(gtt_id)

    w.broker.delete_gtt = flaky
    run = await running(w)
    try:
        opener = await w.rt.gtt.place(opening_buy())
        w.rt.switch.engage("stop", "ayush")
        await until(lambda: w.rt.gtt.get(opener.gtt_id).status is GttStatus.DELETED)
        assert len(calls) >= 2
        await w.log.flush()
        notes = w.notes()
        assert notes.index("GTT_STOP_UNCLEAR") < notes.index("GTT_STOPPED")
    finally:
        await stop(run, w)


async def test_one_that_fires_in_stop_and_adds_risk_has_its_order_cancelled(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)

    async def never(gtt_id):  # the broker won't delete it: it stays armed
        raise TimeoutError

    w.broker.delete_gtt = never
    run = await running(w)
    try:
        opener = await w.rt.gtt.place(opening_buy(price=2420000))  # rests when it fires
        w.rt.switch.engage("stop", "ayush")
        await asyncio.sleep(0.1)
        w.md.on_tick(tick(2530000))  # touched: the broker places the buy
        await until(lambda: w.rt.gtt.get(opener.gtt_id).status is GttStatus.TRIGGERED)
        fired = w.rt.gtt.get(opener.gtt_id)
        order = next(o for o in w.rt.orders.orders() if o.broker_order_id == fired.order_id)
        await until(lambda: w.rt.orders.get(order.client_order_id).status is OrderStatus.CANCELLED)
        assert w.rt.positions.net_qty(FUT) == 0
        await w.log.flush()
        assert "GTT_FIRED_IN_STOP" in w.notes()
    finally:
        await stop(run, w)


async def test_without_stop_nothing_is_swept(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    run = await running(w)
    try:
        opener = await w.rt.gtt.place(opening_buy())
        await asyncio.sleep(0.1)
        assert w.rt.gtt.get(opener.gtt_id).status is GttStatus.ACTIVE
        assert await w.rt.gtt.sweep() == []
    finally:
        await stop(run, w)


async def test_one_failed_delete_doesnt_keep_the_others_armed(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    first = await w.rt.gtt.place(opening_buy())
    second = await w.rt.gtt.place(opening_buy(trigger=2540000, price=2541000))
    real = w.broker.delete_gtt

    async def stuck(gtt_id):
        if gtt_id == first.gtt_id:
            raise TimeoutError
        await real(gtt_id)

    w.broker.delete_gtt = stuck
    w.platform.firm_switch.engage("firm STOP", "risk")  # the firm's switch counts too
    done = await w.rt.gtt.sweep()
    assert len(done) == 2 and "deleting it failed (TimeoutError)" in done[0]
    assert w.rt.gtt.get(second.gtt_id).status is GttStatus.DELETED
    assert w.rt.gtt.get(first.gtt_id).status is GttStatus.ACTIVE  # said, tried again later
    assert "firm STOP" in done[1]
    await w.close()
