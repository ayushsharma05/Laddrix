from dataclasses import replace

from mocktrade.domain.accounts import BrokerAccount, Role, User, can_trade, can_view


def test_owner_with_trader_role_can_trade(trader, account):
    assert can_trade(trader, account)


def test_allowed_family_member_can_trade(account):
    sister = User("u-sis", "Sister", frozenset({Role.TRADER}))
    shared = replace(account, traders=frozenset({sister.id}))
    assert can_trade(sister, shared)
    assert not can_trade(sister, account)


def test_risk_user_can_view_but_not_trade(account):
    risk = User("u-risk", "Risk", frozenset({Role.RISK}))
    assert can_view(risk, account)
    assert not can_trade(risk, account)


def test_inactive_user_or_account_cannot_trade(trader, account):
    assert not can_trade(replace(trader, active=False), account)
    assert not can_trade(trader, replace(account, active=False))


def test_stranger_cannot_view(account):
    stranger = User("u-x", "X", frozenset({Role.TRADER}))
    assert not can_view(stranger, account)
    assert isinstance(account, BrokerAccount)
