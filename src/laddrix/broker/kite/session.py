"""Kite login: turn the account holder's manual login into an access token.

The flow (https://kite.trade/docs/connect/v3/user/):
1. Send the user to the login URL; they log in on Zerodha's own page with 2FA.
2. Kite redirects to our registered URL with `?request_token=...&status=success`.
3. We POST api_key + request_token + checksum to /session/token, where
   checksum = SHA-256(api_key + request_token + api_secret) in hex.
4. The access token works until 6 AM the next day (or until logout).

The login is always done by the account holder. Laddrix never stores Zerodha
passwords or TOTP secrets and never automates the login itself.
"""

import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

from laddrix.broker.kite.http import KiteError, KiteHttp

LOGIN_URL = "https://kite.zerodha.com/connect/login"


@dataclass(frozen=True)
class KiteSession:
    user_id: str  # the Zerodha client id that logged in
    access_token: str = field(repr=False)  # never in a log or an error message (#367)


def login_url(api_key: str, state: str | None = None) -> str:
    """Where to send the account holder. `state` comes back to us in the redirect."""
    query = {"v": "3", "api_key": api_key}
    if state:
        query["redirect_params"] = urlencode({"state": state})
    return f"{LOGIN_URL}?{urlencode(query)}"


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """SHA-256 hex digest of api_key + request_token + api_secret."""
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def request_token_from_redirect(url: str) -> str:
    """Read the request token from the URL Kite redirected to. Raises ValueError if absent."""
    query = parse_qs(urlparse(url).query)
    if query.get("status", ["success"])[0] != "success":
        raise ValueError("Kite reported that the login did not succeed")
    tokens = query.get("request_token")
    if not tokens or not tokens[0]:
        raise ValueError("no request_token in the redirect URL")
    return tokens[0]


async def create_session(http: KiteHttp, request_token: str, api_secret: str) -> KiteSession:
    """Exchange a request token for an access token and remember it on `http`."""
    data = await http.request(
        "POST",
        "/session/token",
        data={
            "api_key": http.api_key,
            "request_token": request_token,
            "checksum": checksum(http.api_key, request_token, api_secret),
        },
        authenticated=False,
    )
    if not data or not data.get("access_token"):
        raise KiteError("Kite did not return an access token")
    http.access_token = data["access_token"]
    return KiteSession(user_id=data.get("user_id", ""), access_token=data["access_token"])
