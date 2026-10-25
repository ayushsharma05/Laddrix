"""The ticker against a real local WebSocket server that behaves like Kite."""

import asyncio
import json
from http import HTTPStatus

import pytest
from kite_packets import NFO_TOKEN, NSE_TOKEN, frame, ltp_packet
from websockets.asyncio.server import serve

from laddrix.broker.kite.http import KiteAuthError
from laddrix.broker.kite.ticker import MAX_TOKENS_PER_CONNECTION, KiteTicker


class FakeKite:
    """A local server. `script(ws, n)` decides what the n-th connection does."""

    def __init__(self, script):
        self.script = script
        self.connections = 0
        self.received: list[list[dict]] = []

    async def handler(self, ws):
        n = self.connections
        self.connections += 1
        self.received.append([])
        await self.script(self, ws, n)

    async def read(self, ws, n, count):
        for _ in range(count):
            self.received[n].append(json.loads(await ws.recv()))


async def no_sleep(_seconds):
    await asyncio.sleep(0)


async def start(fake, **kwargs):
    server = await serve(fake.handler, "127.0.0.1", 0, **kwargs)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def wait_for(condition, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_subscribes_on_connect_and_delivers_ticks():
    async def script(fake, ws, n):
        await fake.read(ws, n, 2)
        await ws.send(frame(ltp_packet(NSE_TOKEN, 250105)))
        await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    got = []
    ticker = KiteTicker("k", "t", got.extend, url=url, sleep=no_sleep)
    await ticker.subscribe([NSE_TOKEN], mode="full")
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: got)
    assert got[0].ltp_paise == 250105
    assert fake.received[0] == [
        {"a": "subscribe", "v": [NSE_TOKEN]},
        {"a": "mode", "v": ["full", [NSE_TOKEN]]},
    ]
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_reconnects_with_backoff_and_resubscribes():
    async def script(fake, ws, n):
        await fake.read(ws, n, 2)
        if n == 0:
            await ws.close()
        else:
            await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)
        await asyncio.sleep(0)

    ticker = KiteTicker("k", "t", lambda ticks: None, url=url, sleep=record_sleep)
    await ticker.subscribe([NSE_TOKEN, NFO_TOKEN], mode="quote")
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: fake.connections == 2 and len(fake.received[1]) == 2)
    assert fake.received[1][0] == {"a": "subscribe", "v": sorted([NSE_TOKEN, NFO_TOKEN])}
    assert waits == [1.0]
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_silence_is_treated_as_a_dead_connection():
    async def script(fake, ws, n):
        await ws.wait_closed()  # never sends anything

    fake = FakeKite(script)
    server, url = await start(fake)
    ticker = KiteTicker("k", "t", lambda ticks: None, url=url, stale_after_s=0.1, sleep=no_sleep)
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: fake.connections >= 2)
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_order_updates_and_broker_messages_from_text_frames():
    order = {"order_id": "250101000001", "status": "COMPLETE", "tag": "MT0001"}

    async def script(fake, ws, n):
        await ws.send(json.dumps({"type": "message", "data": "hello"}))
        await ws.send("not json")
        await ws.send(json.dumps({"type": "order", "data": order}))
        await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    orders = []
    ticker = KiteTicker("k", "t", lambda ticks: None, orders.append, url=url, sleep=no_sleep)
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: orders)
    assert orders == [order]
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_expired_login_stops_retrying():
    def refuse(connection, request):
        return connection.respond(HTTPStatus.FORBIDDEN, "token expired\n")

    fake = FakeKite(lambda *a: None)
    server, url = await start(fake, process_request=refuse)
    ticker = KiteTicker("k", "t", lambda ticks: None, url=url, sleep=no_sleep)
    with pytest.raises(KiteAuthError):
        await asyncio.wait_for(ticker.run(), 3)
    server.close()


async def test_unsubscribe_while_connected_tells_kite():
    async def script(fake, ws, n):
        await fake.read(ws, n, 3)
        await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    ticker = KiteTicker("k", "t", lambda ticks: None, url=url, sleep=no_sleep)
    await ticker.subscribe([NSE_TOKEN], mode="ltp")
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: fake.received and len(fake.received[0]) == 2)
    await ticker.unsubscribe([NSE_TOKEN])
    await wait_for(lambda: len(fake.received[0]) == 3)
    assert fake.received[0][2] == {"a": "unsubscribe", "v": [NSE_TOKEN]}
    assert ticker.subscriptions == {}
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_subscription_rules():
    ticker = KiteTicker("k", "t", lambda ticks: None)
    with pytest.raises(ValueError, match="mode"):
        await ticker.subscribe([1], mode="everything")
    with pytest.raises(ValueError, match="3000"):
        await ticker.subscribe(range(MAX_TOKENS_PER_CONNECTION + 1))
    await ticker.subscribe([1, 2], mode="ltp")
    await ticker.subscribe([2], mode="full")
    assert ticker.subscriptions == {1: "ltp", 2: "full"}


async def test_on_connect_runs_on_every_connection_and_bad_callbacks_never_drop_it():
    async def script(fake, ws, n):
        await ws.send(json.dumps({"type": "order", "data": {"order_id": str(n)}}))
        if n == 0:
            return  # drop the first connection: the ticker reconnects
        await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    connects, orders = [], []

    def broken_order_handler(data):
        orders.append(data)
        raise RuntimeError("handler bug")

    ticker = KiteTicker(
        "k",
        "t",
        lambda ticks: None,
        broken_order_handler,
        on_connect=lambda: connects.append(len(connects)),
        url=url,
        sleep=no_sleep,
    )
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: len(orders) == 2)
    assert connects == [0, 1]  # reconcile hook ran on the reconnect too
    assert ticker.connections == 2  # the failing handler didn't cause the drop
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_prices_in_order_messages_stay_exact():
    async def script(fake, ws, n):
        await ws.send('{"type": "order", "data": {"average_price": 2501.15}}')
        await ws.wait_closed()

    fake = FakeKite(script)
    server, url = await start(fake)
    orders = []
    ticker = KiteTicker("k", "t", lambda ticks: None, orders.append, url=url, sleep=no_sleep)
    task = asyncio.create_task(ticker.run())
    await wait_for(lambda: orders)
    from decimal import Decimal

    assert orders[0]["average_price"] == Decimal("2501.15")
    ticker.stop()
    await asyncio.wait_for(task, 3)
    server.close()


async def test_a_big_frame_is_decoded_in_chunks_but_handed_on_whole():
    """Other work (an order click) runs between chunks; screens get the frame in one go."""
    from laddrix.broker.kite.ticker import CHUNK

    delivered: list[int] = []
    ticker = KiteTicker("k", "t", lambda ticks: delivered.append(len(ticks)))
    others_ran_at: list[int] = []

    async def an_order_click():
        while True:
            others_ran_at.append(len(delivered))
            await asyncio.sleep(0)

    count = CHUNK * 2 + 10
    big = frame(*[ltp_packet(NSE_TOKEN + 256 * n, 100 + n) for n in range(count)])
    other = asyncio.create_task(an_order_click())
    await asyncio.sleep(0)
    before = len(others_ran_at)
    await ticker.handle_frame(big)
    other.cancel()
    assert delivered == [count]  # one hand-over with every tick
    assert len(others_ran_at) - before >= 2  # the click ran between the 3 chunks
    assert all(n == 0 for n in others_ran_at[before:-1])  # ...before the ticks were handed on


async def test_a_bad_frame_is_dropped_whole():
    delivered = []
    ticker = KiteTicker("k", "t", delivered.append)
    await ticker.handle_frame(b"\x00\x02\x00\x08" + b"\x00" * 8 + b"\x00\x50")  # 2nd packet cut
    assert delivered == []
