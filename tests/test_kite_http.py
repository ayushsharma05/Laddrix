"""Talking to Kite's REST API (broker/kite/http.py): headers, forms, each error mapped to its own
exception.
"""

import httpx
import pytest

from laddrix.broker.kite.http import KiteAuthError, KiteError, KiteHttp, KiteRateLimited


def client(handler, token="tok") -> KiteHttp:
    return KiteHttp("key", token, transport=httpx.MockTransport(handler))


async def test_success_returns_data_and_sends_version_and_auth_headers():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(request.headers)
        return httpx.Response(200, json={"status": "success", "data": {"user_id": "AB1234"}})

    http = client(handler)
    assert await http.request("GET", "/user/profile") == {"user_id": "AB1234"}
    assert seen["x-kite-version"] == "3"
    assert seen["authorization"] == "token key:tok"
    await http.aclose()


async def test_none_values_are_not_sent_in_forms():
    body = {}

    def handler(request: httpx.Request):
        body["text"] = request.content.decode()
        return httpx.Response(200, json={"status": "success", "data": {}})

    await client(handler).request("POST", "/orders/regular", data={"qty": 75, "price": None})
    assert body["text"] == "qty=75"


@pytest.mark.parametrize(
    "status, payload, error",
    [
        (
            403,
            {"status": "error", "message": "Token expired", "error_type": "TokenException"},
            KiteAuthError,
        ),
        (429, {"status": "error", "message": "Too many requests"}, KiteRateLimited),
        (
            400,
            {"status": "error", "message": "Invalid price", "error_type": "InputException"},
            KiteError,
        ),
        (502, None, KiteError),
    ],
)
async def test_errors_map_to_specific_exceptions(status, payload, error):
    def handler(request):
        return httpx.Response(status, json=payload) if payload else httpx.Response(status)

    with pytest.raises(error) as caught:
        await client(handler).request("GET", "/orders")
    assert caught.value.status == status


async def test_authenticated_call_without_token_fails_before_sending():
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("request should not be sent")

    with pytest.raises(KiteAuthError):
        await client(handler, token=None).request("GET", "/orders")


async def test_csv_endpoint_returns_text():
    def handler(request):
        return httpx.Response(200, text="instrument_token,tradingsymbol\n1,X\n")

    assert (await client(handler).get_csv("/instruments")).startswith("instrument_token")
