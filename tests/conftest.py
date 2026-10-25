"""Shared test data: a few realistic instruments, users and accounts."""

import os
from datetime import UTC, date, datetime

import pytest

from laddrix.domain.accounts import BrokerAccount, Role, User
from laddrix.domain.instruments import Exchange, Instrument, InstrumentKind
from laddrix.refdata.expiry import Expiries

# The test contracts are dated October 2026 (NIFTY26OCTFUT expires 27 Oct 2026). Tests that
# aren't about expiry, many of them on the real clock, see expiry as on 16 Oct 2026, so they
# don't start failing once that date has passed. Tests about expiry (#290) mark themselves
# `real_expiry` and use the time their own clock gives.
EXPIRY_TESTS_AS_OF = datetime(2026, 10, 16, 4, 30, tzinfo=UTC)  # 10:00 India time


@pytest.fixture(autouse=True)
def _test_contracts_dont_expire(request, monkeypatch):
    if request.node.get_closest_marker("real_expiry"):
        return
    info, expired_by = Expiries.info, Expiries.expired_by
    monkeypatch.setattr(
        Expiries, "info", lambda self, i, now=None: info(self, i, EXPIRY_TESTS_AS_OF)
    )
    monkeypatch.setattr(
        Expiries,
        "expired_by",
        lambda self, day: expired_by(self, min(day, EXPIRY_TESTS_AS_OF.date())),
    )


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
    from laddrix.domain.orders import OrderRequest, OrderType, Product, Side

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


@pytest.fixture(autouse=True, scope="session")
def light_password_hashing():
    """Argon2id is slow on purpose; tests use light settings so they stay fast.
    Production keeps the strong defaults (security.use_hasher is never called there)."""
    from argon2 import PasswordHasher

    from laddrix.server import security

    previous = security.use_hasher(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    yield
    security.use_hasher(previous)


@pytest.fixture(autouse=True)
def _keep_umask():
    """Tools set the process umask (serve, backup); never let that leak into other tests."""
    old = os.umask(0o022)
    os.umask(old)
    yield
    os.umask(old)
