import pytest

from mocktrade.broker.base import Broker, BrokerError
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.orders import OrderStatus, OrderUpdate


def test_fake_broker_follows_the_interface():
    assert isinstance(FakeBroker(), Broker)


async def test_place_returns_ids_and_records_requests(mocker_free_request):
    broker = FakeBroker()
    first = await broker.place(mocker_free_request)
    second = await broker.place(mocker_free_request)
    assert (first, second) == ("F1", "F2")
    assert broker.placed == [mocker_free_request, mocker_free_request]


async def test_scripted_updates_come_out_in_order():
    broker = FakeBroker()
    broker.push(OrderUpdate("MT1", "F1", OrderStatus.OPEN))
    broker.push(OrderUpdate("MT1", "F1", OrderStatus.FILLED, filled_qty=75))
    stream = broker.updates()
    assert (await anext(stream)).status is OrderStatus.OPEN
    assert (await anext(stream)).status is OrderStatus.FILLED


async def test_failure_raises_broker_error_once(mocker_free_request):
    broker = FakeBroker()
    broker.fail_next("insufficient funds")
    with pytest.raises(BrokerError, match="insufficient funds"):
        await broker.place(mocker_free_request)
    assert await broker.place(mocker_free_request) == "F1"
