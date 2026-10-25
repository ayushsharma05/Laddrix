"""The order spec: what can be ordered, published by the server and enforced by it."""

from dataclasses import replace
from decimal import Decimal

import pytest

from laddrix.broker.fake import FakeBroker
from laddrix.broker.rules import order_spec
from laddrix.domain.order_spec import Need, Rule, spec_problem, spec_to_dict, standard_spec
from laddrix.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side, Validity
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds, EventKind
from laddrix.platform import spec_of
from laddrix.refdata.store import InstrumentStore

FUT = "NFO:NIFTY26OCTFUT"


def test_the_standard_spec_has_the_four_nse_bse_order_types(nifty_fut):
    spec = order_spec(nifty_fut)
    assert [t.label for t in spec.order_types] == ["LMT", "MKT", "SL", "SL-M"]
    limit, market, stop_limit, stop_market = spec.order_types
    assert (limit.price, limit.trigger, limit.market_protection) == (
        Need.REQUIRED,
        Need.NONE,
        Need.NONE,
    )
    assert (market.price, market.market_protection) == (Need.NONE, Need.AUTO)
    assert (stop_limit.price, stop_limit.trigger) == (Need.REQUIRED, Need.REQUIRED)
    assert stop_limit.rules == (Rule.STOP_TRIGGER, Rule.STOP_LIMIT)
    assert (stop_market.trigger, stop_market.market_protection) == (Need.REQUIRED, Need.AUTO)
    assert stop_market.validities == (Validity.DAY,)
    # the ladder can place what a click can price: limit and market, not stops
    assert [t.label for t in spec.order_types if t.priced_by_click] == ["LMT", "MKT"]
    assert (spec.tick_paise, spec.lot_size, spec.max_modifications) == (10, 75, 25)
    assert spec.auto_protection_pct == Decimal("2")


def test_products_follow_the_instrument(nifty_fut, reliance, nifty_index):
    assert order_spec(nifty_fut).products == (Product.NRML, Product.MIS)
    assert order_spec(reliance).products == (Product.CNC, Product.MIS)
    index = order_spec(nifty_index)
    assert (index.tradable, index.order_types, index.products) == (False, (), ())


def test_problems_are_named(nifty_fut, nifty_index):
    spec = order_spec(nifty_fut)
    ok = spec_problem(spec, OrderType.LIMIT, Validity.IOC, Product.NRML)
    assert ok is None
    assert "can't be IOC" in spec_problem(spec, OrderType.SL, Validity.IOC, Product.NRML)
    assert "CNC isn't offered" in spec_problem(spec, OrderType.LIMIT, Validity.DAY, Product.CNC)
    assert "can't be traded" in spec_problem(
        order_spec(nifty_index), OrderType.LIMIT, Validity.DAY, Product.MIS
    )
    fewer = replace(spec, order_types=spec.order_types[:2])  # a broker without stops
    assert "SL orders aren't offered" in spec_problem(
        fewer, OrderType.SL, Validity.DAY, Product.NRML
    )


def test_the_json_the_screens_read(nifty_fut):
    d = spec_to_dict(order_spec(nifty_fut))
    assert d["order_types"][3] == {
        "code": "SL-M",
        "label": "SL-M",
        "name": "Stop-loss market",
        "help": d["order_types"][3]["help"],
        "price": "none",
        "trigger": "required",
        "market_protection": "auto",
        "validities": ["DAY"],
        "rules": ["stop_trigger"],
        "priced_by_click": False,
    }
    assert (d["products"], d["auto_protection_pct"], d["tick_paise"]) == (["NRML", "MIS"], "2", 10)


def test_a_broker_can_publish_its_own_spec_and_others_get_the_standard(nifty_fut):
    class NoStops(FakeBroker):
        def order_spec(self, instrument):
            spec = standard_spec(instrument, auto_protection_pct=Decimal("1"), max_modifications=10)
            return replace(spec, order_types=spec.order_types[:2])

    assert [t.label for t in spec_of(NoStops())(nifty_fut).order_types] == ["LMT", "MKT"]
    assert spec_of(FakeBroker())(nifty_fut) == order_spec(nifty_fut)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"product": Product.CNC}, "CNC isn't offered"),
        (
            {"order_type": OrderType.SL, "trigger_paise": 2500500, "validity": Validity.IOC},
            "can't be IOC",
        ),
    ],
)
async def test_the_order_manager_refuses_what_the_spec_does_not_offer(nifty_fut, change, reason):
    store = InstrumentStore()
    store.add(nifty_fut)
    broker = FakeBroker()
    events = []
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), spec=order_spec)
    om.subscribe(events.append)
    request = OrderRequest(
        client_order_id="",
        account_id="acc-1",
        user_id="ayush",
        instrument_id=FUT,
        side=Side.BUY,
        qty=75,
        order_type=OrderType.LIMIT,
        product=Product.NRML,
        price_paise=2501000,
    )
    view = await om.submit(replace(request, **change))
    assert view.status is OrderStatus.REJECTED and reason in view.reason
    assert broker.placed == []  # never reached the broker
    assert [e.kind for e in events] == [EventKind.SUBMITTED, EventKind.REJECTED]  # audited
