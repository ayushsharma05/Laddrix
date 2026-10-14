"""Watch a live ladder in the terminal: python -m mocktrade.tools.watch NFO:NIFTY26OCTFUT

Needs today's Kite login (python -m mocktrade.tools.login) and the Kite data plan.
"""

import asyncio
import sys
from datetime import UTC, datetime

from mocktrade.broker.kite import token_store
from mocktrade.broker.kite.feed import KiteFeed
from mocktrade.broker.kite.http import KiteAuthError, KiteHttp
from mocktrade.broker.kite.instruments import load_kite_instruments
from mocktrade.broker.kite.ticker import KiteTicker
from mocktrade.config import KiteSettings, environment
from mocktrade.marketdata.ladder import render_ladder
from mocktrade.marketdata.service import MarketData
from mocktrade.refdata.store import InstrumentStore


async def main(instrument_ids: list[str]) -> None:
    settings = KiteSettings.from_env(environment())
    session = token_store.load(settings.token_file, datetime.now(UTC))
    if session is None:
        raise KiteAuthError("no valid Kite login today: run python -m mocktrade.tools.login")

    http = KiteHttp(settings.api_key, session.access_token)
    store = InstrumentStore()
    try:
        report = load_kite_instruments(await http.get_csv("/instruments"), store)
    finally:
        await http.aclose()
    print(f"instruments loaded: {report.loaded}")

    market_data = MarketData()
    feed = KiteFeed(store, market_data)
    ticker = KiteTicker(settings.api_key, session.access_token, feed.on_ticks)
    await ticker.subscribe(feed.tokens_for(instrument_ids), mode="full")
    subscription = market_data.subscribe(instrument_ids)
    runner = asyncio.create_task(ticker.run())
    try:
        while True:
            for tick in await subscription.next_batch():
                print("\033[2J\033[H" + render_ladder(tick), flush=True)
    finally:
        ticker.stop()
        runner.cancel()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m mocktrade.tools.watch NFO:NIFTY26OCTFUT [more ids]")
    asyncio.run(main(sys.argv[1:]))
