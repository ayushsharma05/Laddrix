"""A small async client for Kite Connect's REST API (v3).

Why our own client instead of the official SDK: the SDK is synchronous and pulls in
Twisted for its ticker; MockTrade is asyncio throughout. Kite's REST API is simple:
form-encoded requests, JSON replies shaped like

    {"status": "success", "data": {...}}
    {"status": "error", "message": "...", "error_type": "TokenException"}

Every request carries `X-Kite-Version: 3`; authenticated ones carry
`Authorization: token <api_key>:<access_token>`.
"""

import json
from decimal import Decimal
from typing import Any

import httpx

BASE_URL = "https://api.kite.trade"


class KiteError(Exception):
    """Kite answered with an error. `error_type` is Kite's own category."""

    def __init__(self, message: str, error_type: str = "GeneralException", status: int = 0):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status = status


class KiteAuthError(KiteError):
    """The session is missing, expired (6 AM daily) or was logged out. Log in again."""


class KiteRateLimited(KiteError):
    """Kite refused because a rate limit was hit (HTTP 429)."""


class KiteHttp:
    def __init__(
        self,
        api_key: str,
        access_token: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout_s: float = 7.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"X-Kite-Version": "3"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        """Send one request and return the `data` part of Kite's reply."""
        headers = self._auth_headers() if authenticated else {}
        form = {k: v for k, v in (data or {}).items() if v is not None}
        response = await self._client.request(
            method, path, data=form or None, params=params, headers=headers
        )
        return _unwrap(response)

    async def get_csv(self, path: str) -> str:
        """GET an endpoint that answers with CSV instead of JSON (e.g. /instruments)."""
        response = await self._client.get(path, headers=self._auth_headers())
        if not response.is_success:
            _unwrap(response)  # raises the right error
        return response.text

    def _auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            raise KiteAuthError("not logged in to Kite", "TokenException")
        return {"Authorization": f"token {self.api_key}:{self.access_token}"}


def _unwrap(response: httpx.Response) -> Any:
    try:
        body = json.loads(response.content, parse_float=Decimal)  # prices stay exact
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if response.status_code == 429:
        raise KiteRateLimited(body.get("message", "rate limited"), "NetworkException", 429)
    if response.is_success and body.get("status") == "success":
        return body.get("data")
    message = body.get("message") or f"HTTP {response.status_code}"
    error_type = body.get("error_type", "GeneralException")
    if error_type == "TokenException" or response.status_code == 403:
        raise KiteAuthError(message, error_type, response.status_code)
    raise KiteError(message, error_type, response.status_code)
