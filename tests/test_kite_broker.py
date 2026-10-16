"""KiteBroker against a fake Kite REST server: requests, error meaning, updates, reconcile."""

import asyncio
import json
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.broker.kite.broker import AMO, KiteBroker, KiteUnknownOutcome
from mocktrade.broker.kite.http import KiteHttp
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds, Pending
from mocktrade.refdata.store import InstrumentStore

S = OrderStatus

REQUEST = OrderRequest(
    client_order_id="MT1",
    account_id="acc-1",
    user_id="u-ayush",
    instrument_id="NFO:NIFTY26OCTFUT",
    side=Side.BUY,
    qty=75,
    order_type=OrderType.LIMIT,
    product=Product.NRML,
    price_paise=2501010,
)


class FakeKiteRest:
    """Records every request; answers from `responses` (method, path) -> list of replies."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        self.responses: dict[tuple[str, str], list] = {}

    def reply(self, method, path, *replies):
        self.responses.setdefault((method, path), []).extend(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((request.method, request.url.path, form))
        replies = self.responses.get((request.method, request.url.path), [])
        reply = replies.pop(0) if replies else ok({})
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(data):
    return httpx.Response(200, json={"status": "success", "data": data})


def error(status, error_type, message):
    body = {"status": "error", "error_type": error_type, "message": message}
    return httpx.Response(status, json=body)


async def no_sleep(_):
    pass


@pytest.fixture
def kite():
    return FakeKiteRest()


@pytest.fixture
def broker(kite):
    http = KiteHttp("key", "token", transport=httpx.MockTransport(kite.handler))
    return KiteBroker(http, kite_user_id="AB1234", sleep=no_sleep)


def test_kite_broker_follows_the_broker_interface(broker):
    assert isinstance(broker, Broker)


# --- placing, modifying, cancelling ---


async def test_place_posts_the_order_and_returns_kites_id(broker, kite):
    kite.reply("POST", "/orders/regular", ok({"order_id": "251014000000001"}))
    assert await broker.place(REQUEST) == "251014000000001"
    method, path, form = kite.requests[0]
    assert (method, path) == ("POST", "/orders/regular")
    assert form["tag"] == "MT1" and form["price"] == "25010.10" and form["quantity"] == "75"


async def test_after_market_orders_remember_their_variety(broker, kite):
    broker.variety = AMO
    kite.reply("POST", "/orders/amo", ok({"order_id": "9"}))
    await broker.place(REQUEST)
    broker.variety = "regular"
    await broker.modify("9", price_paise=2500000)
    await broker.cancel("9")
    assert [(m, p) for m, p, _ in kite.requests] == [
        ("POST", "/orders/amo"),
        ("PUT", "/orders/amo/9"),
        ("DELETE", "/orders/amo/9"),
    ]
    assert kite.requests[1][2] == {"price": "25000.00"}


@pytest.mark.parametrize(
    "reply, meaning",
    [
        (error(400, "InputException", "Invalid price"), "refused"),
        (error(400, "OrderException", "Order not found"), "refused"),
        (error(400, "MarginException", "Insufficient funds"), "refused"),
        (error(400, "HoldingException", "No holdings"), "refused"),
        (error(403, "TokenException", "Session expired"), "refused"),
        (error(429, "NetworkException", "Too many requests"), "refused"),
        (error(502, "NetworkException", "OMS unreachable"), "unknown"),
        (error(500, "DataException", "Couldn't parse OMS response"), "unknown"),
        (error(400, "DataException", "Couldn't parse OMS response"), "unknown"),  # may be live!
        (error(400, "SomeNewException", "?"), "unknown"),  # unfamiliar: never assume
        (error(500, "GeneralException", "Something went wrong"), "unknown"),
        (error(503, "InputException", "Service unavailable"), "unknown"),
        (httpx.ReadTimeout("timed out"), "unknown"),
        (httpx.ConnectError("refused"), "unknown"),
    ],
)
async def test_which_errors_mean_not_placed_and_which_mean_unknown(broker, kite, reply, meaning):
    kite.reply("POST", "/orders/regular", reply)
    expected = BrokerError if meaning == "refused" else KiteUnknownOutcome
    with pytest.raises(expected):
        await broker.place(REQUEST)
    assert len(kite.requests) == 1  # never retried by itself


async def test_refusal_reasons_are_readable(broker, kite):
    kite.reply("POST", "/orders/regular", error(400, "MarginException", "Insufficient funds."))
    kite.reply("POST", "/orders/regular", error(403, "TokenException", "Session expired"))
    with pytest.raises(BrokerError, match="^Insufficient funds.$"):
        await broker.place(REQUEST)
    with pytest.raises(BrokerError, match="Kite login needed: Session expired"):
        await broker.place(REQUEST)


async def test_unknown_outcome_leaves_the_order_unconfirmed_then_reconcile_finds_it(kite, broker):
    store = InstrumentStore()
    from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind

    store.add(Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, 10, 75))
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"))
    kite.reply("POST", "/orders/regular", error(502, "NetworkException", "OMS unreachable"))
    view = await om.submit(REQUEST)
    assert (view.status, view.pending) == (S.PENDING, Pending.UNCONFIRMED)
    kite.reply(
        "GET",
        "/orders",
        ok([{"order_id": "77", "status": "OPEN", "tag": "MT1", "filled_quantity": 0}]),
    )
    await om.resync()
    found = om.get("MT1")
    assert (found.status, found.broker_order_id, found.pending) == (S.OPEN, "77", Pending.NONE)


# --- updates from the WebSocket ---


async def test_order_messages_become_updates(broker):
    broker.on_order_message(
        {
            "user_id": "AB1234",
            "order_id": "77",
            "status": "COMPLETE",
            "tag": "MT1",
            "filled_quantity": 75,
            "average_price": Decimal("25010.10"),
            "variety": "amo",
        }
    )
    update = await asyncio.wait_for(anext(broker.updates()), 1)
    assert (update.status, update.filled_qty, update.avg_price_paise) == (S.FILLED, 75, 2501010)
    await broker.cancel("77")  # variety learned from the message


async def test_messages_for_another_user_or_unreadable_are_ignored(broker):
    broker.on_order_message({"user_id": "ZZ9999", "order_id": "1", "status": "OPEN"})
    broker.on_order_message({"user_id": "AB1234", "order_id": "2", "status": "BRAND NEW"})
    broker.on_order_message({"user_id": "AB1234", "status": "OPEN"})  # no order id
    broker.on_order_message({"order_id": "3", "status": "OPEN", "filled_quantity": "x"})
    assert broker.ignored_messages == 3
    # a status we don't know is passed on, never dropped (#259): proof the order exists
    kept = await asyncio.wait_for(anext(broker.updates()), 0.5)
    assert (kept.broker_order_id, kept.status) == ("2", S.PENDING)
    assert "doesn't know: BRAND NEW" in kept.reason
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(broker.updates()), 0.05)


# --- reading the order book and positions ---


async def test_orders_reads_the_book_and_keeps_unknown_statuses(broker, kite):
    kite.reply(
        "GET",
        "/orders",
        ok(
            [
                {"order_id": "1", "status": "COMPLETE", "tag": "MT1", "filled_quantity": 75},
                {"order_id": "2", "status": "WEIRD"},
                {"order_id": "3", "status": "TRIGGER PENDING", "tag": None, "variety": "amo"},
            ]
        ),
    )
    orders = await broker.orders()
    assert [(o.broker_order_id, o.status) for o in orders] == [
        ("1", S.FILLED),
        ("2", S.PENDING),
        ("3", S.OPEN),
    ]  # an unknown status kept (#259)
    await broker.cancel("3")
    assert kite.requests[-1][1] == "/orders/amo/3"


async def test_reads_are_retried_when_kite_hiccups(broker, kite):
    kite.reply("GET", "/orders", httpx.ReadTimeout("slow"), error(502, "NetworkException", "x"))
    kite.reply("GET", "/orders", ok([]))
    assert await broker.orders() == []
    assert len(kite.requests) == 3


async def test_reads_give_up_cleanly(broker, kite):
    kite.reply("GET", "/orders", *[httpx.ConnectError("down")] * 3)
    with pytest.raises(BrokerError, match="Kite /orders"):
        await broker.orders()
    kite.reply("GET", "/orders", error(403, "TokenException", "expired"))
    with pytest.raises(BrokerError, match="expired"):
        await broker.orders()


async def test_positions_come_from_the_net_book(broker, kite):
    kite.reply(
        "GET",
        "/portfolio/positions",
        httpx.Response(
            200,
            content=json.dumps(
                {
                    "status": "success",
                    "data": {
                        "net": [
                            {
                                "exchange": "NFO",
                                "tradingsymbol": "NIFTY26OCTFUT",
                                "quantity": -75,
                                "average_price": 25010.15,
                                "overnight_quantity": -150,
                                "close_price": 24990.5,
                                "product": "NRML",
                            }
                        ],
                        "day": [],
                    },
                }
            ),
        ),
    )
    (position,) = await broker.positions()
    assert (position.instrument_id, position.qty, position.avg_price_paise) == (
        "NFO:NIFTY26OCTFUT",
        -75,
        2501015,
    )
    # what was carried in from earlier days, for rebuilding after a restart (#206)
    assert (position.overnight_qty, position.close_paise, position.product) == (
        -150,
        2499050,
        Product.NRML,
    )
