"""Kite order translation, checked against the official SDK where it applies."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from kiteconnect import KiteConnect

from mocktrade.broker.kite.orders import (
    STATUS_MAP,
    modify_params,
    place_params,
    to_kite_rupees,
    to_paise,
    to_update,
)
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
    Validity,
)

S = OrderStatus
IST = timezone(timedelta(hours=5, minutes=30))

LIMIT = OrderRequest(
    client_order_id="MT26101442",
    account_id="acc-1",
    user_id="u-ayush",
    instrument_id="NFO:NIFTY26OCTFUT",
    side=Side.BUY,
    qty=75,
    order_type=OrderType.LIMIT,
    product=Product.NRML,
    price_paise=2501010,
)


def sdk_place_params(**kwargs) -> dict:
    """What the official SDK would send for place_order (captured, not sent)."""
    captured = {}
    kite = KiteConnect("key")

    def fake_post(route, url_args=None, params=None, **_):
        captured.update(params)
        return {"order_id": "1"}

    kite._post = fake_post
    kite.place_order(**kwargs)
    captured.pop("variety", None)  # goes in the URL, not the form
    return {k: str(v) for k, v in captured.items()}


# --- money ---


@pytest.mark.parametrize(
    "paise, rupees",
    [(2501010, "25010.10"), (5, "0.05"), (100, "1.00"), (None, None)],
)
def test_paise_to_kite_rupees_is_exact(paise, rupees):
    assert to_kite_rupees(paise) == rupees


@pytest.mark.parametrize(
    "value, paise",
    [
        (Decimal("2501.10"), 250110),
        ("2501.1", 250110),
        (2501.1, 250110),  # floats go through str(): no 250109.99999 surprises
        (Decimal("72.755"), 7276),  # fractional paise in averages round half up
        (72.755, 7276),  # as a float it's 72.75499999…: str() recovers what Kite sent
        (Decimal("72.754"), 7275),
        (0, 0),
        (None, 0),
        ("", 0),
    ],
)
def test_kite_rupees_to_paise(value, paise):
    assert to_paise(value) == paise


# --- placing ---


def test_limit_order_params_match_the_official_sdk():
    ours = {k: str(v) for k, v in place_params(LIMIT).items()}
    sdk = sdk_place_params(
        variety="regular",
        exchange="NFO",
        tradingsymbol="NIFTY26OCTFUT",
        transaction_type="BUY",
        quantity=75,
        product="NRML",
        order_type="LIMIT",
        price="25010.10",
        validity="DAY",
        tag="MT26101442",
    )
    assert ours == sdk


def test_stop_market_order_with_protection_and_algo_id_matches_the_sdk():
    request = replace(
        LIMIT,
        side=Side.SELL,
        order_type=OrderType.SL_M,
        price_paise=None,
        trigger_paise=2500500,
        market_protection=Decimal("-1"),
        validity=Validity.IOC,
        product=Product.MIS,
        algo_id="ALGO123",
    )
    ours = {k: str(v) for k, v in place_params(request).items()}
    sdk = sdk_place_params(
        variety="regular",
        exchange="NFO",
        tradingsymbol="NIFTY26OCTFUT",
        transaction_type="SELL",
        quantity=75,
        product="MIS",
        order_type="SL-M",
        trigger_price="25005.00",
        validity="IOC",
        tag="MT26101442",
        market_protection="-1",
        algo_id="ALGO123",
    )
    assert ours == sdk


def test_symbols_with_spaces_or_dashes_survive():
    request = replace(LIMIT, instrument_id="NSE:M&M-BE")
    params = place_params(request)
    assert (params["exchange"], params["tradingsymbol"]) == ("NSE", "M&M-BE")


def test_modify_sends_only_what_changes():
    assert modify_params(150, None, None) == {"quantity": 150}
    assert modify_params(None, 2501000, 2500500) == {
        "price": "25010.00",
        "trigger_price": "25005.00",
    }


# --- reading Kite's orders ---

KITE_ORDER = {
    "order_id": "251014000123456",
    "status": "OPEN",
    "tag": "MT26101442",
    "filled_quantity": 0,
    "average_price": 0,
    "quantity": 150,
    "order_timestamp": "2026-10-14 09:15:02",
    "exchange_update_timestamp": "2026-10-14 09:15:03",
    "status_message": None,
    "variety": "regular",
}


@pytest.mark.parametrize(
    "kite_status, ours",
    [
        ("COMPLETE", S.FILLED),
        ("REJECTED", S.REJECTED),
        ("CANCELLED", S.CANCELLED),
        ("OPEN", S.OPEN),
        ("TRIGGER PENDING", S.OPEN),
        ("MODIFY PENDING", S.OPEN),
        ("CANCEL PENDING", S.OPEN),
        ("UPDATE", S.OPEN),
        ("PUT ORDER REQ RECEIVED", S.PENDING),
        ("VALIDATION PENDING", S.PENDING),
        ("OPEN PENDING", S.PENDING),
        ("AMO REQ RECEIVED", S.PENDING),
        ("complete", S.FILLED),  # case doesn't matter
    ],
)
def test_kite_statuses_map_to_ours(kite_status, ours):
    assert to_update({**KITE_ORDER, "status": kite_status}).status is ours


def test_every_mapped_status_is_one_we_know():
    assert set(STATUS_MAP.values()) <= set(S)


def test_an_unknown_future_status_is_skipped_not_fatal(caplog):
    assert to_update({**KITE_ORDER, "status": "SOMETHING NEW"}) is None
    assert "unknown Kite order status" in caplog.text


def test_a_kite_order_becomes_our_update():
    update = to_update(
        {
            **KITE_ORDER,
            "status": "OPEN",
            "filled_quantity": 75,
            "average_price": Decimal("25010.15"),
        }
    )
    assert update.client_order_id == "MT26101442"
    assert update.broker_order_id == "251014000123456"
    assert (update.status, update.filled_qty, update.avg_price_paise) == (S.OPEN, 75, 2501015)
    assert update.ts == datetime(2026, 10, 14, 9, 15, 3, tzinfo=IST)  # India time, not UTC


def test_rejection_and_cancel_reasons():
    rejected = to_update(
        {**KITE_ORDER, "status": "REJECTED", "status_message": "Insufficient funds."}
    )
    assert rejected.reason == "Insufficient funds."
    raw_only = to_update({**KITE_ORDER, "status": "REJECTED", "status_message_raw": "RMS:x"})
    assert raw_only.reason == "RMS:x"
    assert to_update({**KITE_ORDER, "status": "CANCELLED"}).reason == "cancelled"
    assert to_update({**KITE_ORDER, "status": "OPEN", "status_message": "x"}).reason == ""


def test_ioc_cancelled_after_a_part_fill_keeps_the_fill():
    update = to_update(
        {**KITE_ORDER, "status": "CANCELLED", "filled_quantity": 75, "average_price": "25010"}
    )
    assert (update.status, update.filled_qty, update.avg_price_paise) == (S.CANCELLED, 75, 2501000)


def test_tag_from_the_newer_tags_list_or_missing():
    no_tag = {k: v for k, v in KITE_ORDER.items() if k != "tag"}
    assert to_update({**no_tag, "tags": ["MT9"]}).client_order_id == "MT9"
    assert to_update(no_tag).client_order_id == ""  # an order placed outside MockTrade


def test_timestamps_from_the_sdk_parsed_form_or_missing():
    parsed = to_update({**KITE_ORDER, "exchange_update_timestamp": datetime(2026, 10, 14, 10, 0)})
    assert parsed.ts == datetime(2026, 10, 14, 10, 0, tzinfo=IST)
    bare = {k: v for k, v in KITE_ORDER.items() if "timestamp" not in k}
    assert to_update(bare).ts.tzinfo is not None
