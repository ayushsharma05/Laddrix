import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mocktrade.broker.kite.http import KiteHttp
from mocktrade.broker.kite.session import (
    checksum,
    create_session,
    login_url,
    request_token_from_redirect,
)


def test_checksum_is_sha256_of_key_token_secret():
    # Known answer computed independently: sha256("abc" + "req" + "sec")
    expected = hashlib.sha256(b"abcreqsec").hexdigest()
    assert checksum("abc", "req", "sec") == expected
    assert len(expected) == 64


def test_login_url_carries_version_key_and_state():
    url = login_url("abc", state="u-ayush")
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://kite.zerodha.com/connect/login?")
    assert query["v"] == ["3"] and query["api_key"] == ["abc"]
    assert query["redirect_params"] == ["state=u-ayush"]


def test_request_token_is_read_from_the_redirect():
    url = "https://mocktrade.example/kite/callback?action=login&status=success&request_token=XYZ"
    assert request_token_from_redirect(url) == "XYZ"


@pytest.mark.parametrize(
    "url",
    [
        "https://x/cb?status=error&request_token=XYZ",
        "https://x/cb?status=success",
        "https://x/cb?status=success&request_token=",
    ],
)
def test_bad_redirects_are_refused(url):
    with pytest.raises(ValueError):
        request_token_from_redirect(url)


async def test_create_session_posts_checksum_and_keeps_the_token():
    sent = {}

    def handler(request: httpx.Request):
        sent.update(parse_qs(request.content.decode()))
        sent["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"status": "success", "data": {"user_id": "AB1234", "access_token": "ACC"}}
        )

    http = KiteHttp("abc", transport=httpx.MockTransport(handler))
    session = await create_session(http, "req", "sec")
    assert (session.user_id, session.access_token) == ("AB1234", "ACC")
    assert http.access_token == "ACC"
    assert sent["checksum"] == [checksum("abc", "req", "sec")]
    assert sent["auth"] is None  # the token exchange itself is not authenticated
