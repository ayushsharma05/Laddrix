"""Shared test data: a few realistic instruments, users and accounts."""

from datetime import date

import pytest

from mocktrade.domain.accounts import BrokerAccount, Role, User
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind


@pytest.fixture
def reliance() -> Instrument:
    return Instrument(Exchange.NSE, "RELIANCE", InstrumentKind.EQUITY, tick_paise=10, lot_size=1)


@pytest.fixture
def nifty_fut() -> Instrument:
    return Instrument(
        Exchange.NFO,
        "NIFTY26OCTFUT",
        InstrumentKind.FUTURE,
        tick_paise=10,
        lot_size=75,
        expiry=date(2026, 10, 27),
        underlying="NIFTY",
    )


@pytest.fixture
def nifty_index() -> Instrument:
    return Instrument(Exchange.NSE, "NIFTY 50", InstrumentKind.INDEX, tick_paise=5, lot_size=1)


@pytest.fixture
def trader() -> User:
    return User("u-ayush", "Ayush", frozenset({Role.TRADER, Role.ADMIN}))


@pytest.fixture
def account(trader: User) -> BrokerAccount:
    return BrokerAccount("acc-1", "kite", "AB1234", owner_user_id=trader.id)


@pytest.fixture
def mocker_free_request(nifty_fut):
    """A simple valid order request used by broker tests."""
    from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side

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
