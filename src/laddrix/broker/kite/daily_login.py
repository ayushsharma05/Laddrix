"""Kite's daily login, started from the web page instead of the terminal.

    screen ─► "Log in to Kite" ─► Zerodha's own login page (password + 2FA there)
           ◄─ Kite redirects to /kite/callback?request_token=...&state=...
    server ─► create_session ─► check it's THIS account's client id ─► save token

Each broker account has its own Kite app (a retail Kite app works for one client
id), so settings are looked up per account. Zerodha passwords and TOTP secrets are
never seen or stored by Laddrix: the account holder logs in on Zerodha's page.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from laddrix.broker.kite import token_store
from laddrix.broker.kite.http import KiteHttp, http_for
from laddrix.broker.kite.session import KiteSession, create_session, login_url
from laddrix.config import KiteSettings
from laddrix.domain.accounts import BrokerAccount


class WrongKiteUser(Exception):
    """Someone logged in to Zerodha as a different client than this account."""


class KiteDailyLogin:
    def __init__(
        self,
        apps: Callable[[BrokerAccount], KiteSettings],
        token_dir: Path,
        *,
        on_session: Callable[[BrokerAccount, KiteSession], Awaitable[None]] | None = None,
        http_factory: Callable[[str], KiteHttp] = KiteHttp,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._apps = apps
        self._token_dir = token_dir
        self._on_session = on_session
        self._http_factory = http_factory
        self._clock = clock

    def token_file(self, account: BrokerAccount) -> Path:
        return self._token_dir / f"kite_token_{account.id}.json"

    def login_url(self, account: BrokerAccount, state: str) -> str:
        return login_url(self._apps(account).api_key, state)

    async def complete(self, account: BrokerAccount, request_token: str) -> None:
        app = self._apps(account)
        http = http_for(self._http_factory, app)  # the login leaves the way its orders do
        try:
            session = await create_session(http, request_token, app.api_secret)
        finally:
            await http.aclose()
        if session.user_id != account.broker_client_id:
            raise WrongKiteUser(
                f"logged in to Zerodha as {session.user_id}, but account {account.id} "
                f"is {account.broker_client_id}"
            )
        token_store.save(self.token_file(account), session, self._clock())
        if self._on_session is not None:
            await self._on_session(account, session)
