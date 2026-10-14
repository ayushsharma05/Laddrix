from dataclasses import replace
from decimal import Decimal

import pytest

from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
    Validity,
)


@pytest.fixture
def limit_buy(nifty_fut) -> OrderRequest:
    return OrderRequest(
        client_order_id="MT0001",
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=nifty_fut.id,
        side=Side.BUY,
        qty=75,
        order_type=OrderType.LIMIT,
        product=Product.NRML,
        price_paise=2501000,
    )


def test_valid_limit_order_passes(limit_buy, nifty_fut):
    limit_buy.validate(nifty_fut)


@pytest.mark.parametrize(
    "changes",
    [
        {"client_order_id": "this-has-dashes"},
        {"client_order_id": "A" * 21},
        {"qty": 50},
        {"price_paise": None},
        {"price_paise": 2501005},
        {"trigger_paise": 2500000},
        {"market_protection": Decimal("2")},
        {"instrument_id": "NSE:RELIANCE"},
    ],
)
def test_broken_limit_orders_are_rejected(limit_buy, nifty_fut, changes):
    with pytest.raises(ValueError):
        replace(limit_buy, **changes).validate(nifty_fut)


@pytest.mark.parametrize("protection", [Decimal("-1"), Decimal("2"), Decimal("100")])
def test_market_order_with_valid_protection_passes(limit_buy, nifty_fut, protection):
    market = replace(
        limit_buy, order_type=OrderType.MARKET, price_paise=None, market_protection=protection
    )
    market.validate(nifty_fut)


@pytest.mark.parametrize("protection", [None, Decimal("0"), Decimal("101"), Decimal("-2")])
def test_market_order_without_valid_protection_is_rejected(limit_buy, nifty_fut, protection):
    market = replace(
        limit_buy, order_type=OrderType.MARKET, price_paise=None, market_protection=protection
    )
    with pytest.raises(ValueError):
        market.validate(nifty_fut)


def test_stop_loss_needs_a_trigger(limit_buy, nifty_fut):
    with pytest.raises(ValueError):
        replace(limit_buy, order_type=OrderType.SL).validate(nifty_fut)
    replace(limit_buy, order_type=OrderType.SL, trigger_paise=2500500).validate(nifty_fut)


def test_indices_cannot_be_ordered(limit_buy, nifty_index):
    order = replace(limit_buy, instrument_id=nifty_index.id, qty=1, price_paise=2500000)
    with pytest.raises(ValueError):
        order.validate(nifty_index)


def test_final_statuses():
    assert OrderStatus.FILLED.is_final
    assert not OrderStatus.PARTIAL.is_final


def test_side_sign():
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1
    assert Validity.IOC.value == "IOC"
