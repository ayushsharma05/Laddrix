"""Log in to Kite from the terminal: python -m laddrix.tools.login

Prints Zerodha's login URL, waits for you to paste the URL you were redirected to,
then saves today's access token to the token file. Never prints secrets or tokens.
"""

import asyncio
from datetime import UTC, datetime

from laddrix.broker.kite import token_store
from laddrix.broker.kite.http import KiteHttp
from laddrix.broker.kite.session import create_session, login_url, request_token_from_redirect
from laddrix.config import KiteSettings, environment


async def main() -> None:
    settings = KiteSettings.from_env(environment())
    print("1. Open this URL and log in with your Zerodha account:\n")
    print(f"   {login_url(settings.api_key)}\n")
    redirected = input("2. Paste the full URL you were redirected to: ").strip()
    request_token = request_token_from_redirect(redirected)
    http = KiteHttp(settings.api_key)
    try:
        session = await create_session(http, request_token, settings.api_secret)
    finally:
        await http.aclose()
    token_store.save(settings.token_file, session, datetime.now(UTC))
    print(
        f"Logged in as {session.user_id}. Token saved to {settings.token_file} (valid until 6 AM)."
    )


if __name__ == "__main__":
    asyncio.run(main())
