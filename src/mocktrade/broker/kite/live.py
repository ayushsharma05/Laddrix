"""Live Kite inside the server: each kite account's connection, from start-up to 6 AM.

    start-up   yesterday's cached instrument list ─► store (until a login refreshes it)
               a saved token still valid today?   ─► go live
    login      the daily login from the screen     ─► go live, no restart
    go live    the token goes into the account's KiteHttp: its orders work
               the first live account downloads today's instruments and carries the shared
               PRICES (charts, option chains: per account in #395b)
               every live account's ticker carries its own ORDER updates and its OWN PRICES
               into its own MarketData (#395): that account's checks, algos, positions and
               its owner's screens read only those, never another subscription's
    demand     a screen opens a contract ─► its token is subscribed at once (full mode)
    reconnect  order updates may have been missed ─► that account's orders are resynced
    6 AM       the token expires ─► orders refused ("log in to Kite"), ticker stopped;
               another live account takes over prices if there is one

Accounts are known from start-up (their KiteBroker exists without a token), so the
platform runs them from the first moment and a login only switches them on.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mocktrade.broker.kite import token_store
from mocktrade.broker.kite.broker import KiteBroker
from mocktrade.broker.kite.daily_login import KiteDailyLogin
from mocktrade.broker.kite.feed import KiteFeed
from mocktrade.broker.kite.http import BASE_URL, KiteAuthError, KiteHttp, http_for
from mocktrade.broker.kite.instruments import load_kite_instruments
from mocktrade.broker.kite.session import KiteSession
from mocktrade.broker.kite.ticker import WS_URL, KiteTicker
from mocktrade.config import KiteSettings
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.instruments import InstrumentId
from mocktrade.marketdata.service import MarketData
from mocktrade.metrics import freeze_heap
from mocktrade.refdata.store import InstrumentStore

log = logging.getLogger(__name__)

LIVE = "live"
LOGIN_NEEDED = "login_needed"


@dataclass
class _Link:
    """One kite account's connection."""

    account: BrokerAccount
    app: KiteSettings
    http: KiteHttp
    broker: KiteBroker
    prices: MarketData  # this account's own subscription's prices (#395)
    feed: KiteFeed  # its ticks, decoded into `prices`
    ticker: KiteTicker | None = None
    task: asyncio.Task | None = None
    expires: datetime | None = None

    @property
    def live(self) -> bool:
        return self.expires is not None


class KiteLive:
    def __init__(
        self,
        apps: Callable[[BrokerAccount], KiteSettings],
        token_dir: Path,
        store: InstrumentStore,
        market_data: MarketData,
        *,
        instruments_cache: Path,
        on_reconnect: Callable[[str], None] | None = None,
        http_factory: Callable[..., KiteHttp] | None = None,
        ticker_factory: Callable[..., KiteTicker] | None = None,
        api_url: str = BASE_URL,  # another address only for a Kite simulator (load tests)
        ws_url: str = WS_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._apps = apps
        self._store = store
        self._md = market_data
        self._cache = instruments_cache
        self._on_reconnect = on_reconnect
        self._http_factory = http_factory or (
            lambda key, token=None, proxy=None: KiteHttp(key, token, base_url=api_url, proxy=proxy)
        )
        self._ticker_factory = ticker_factory or (
            lambda *args, **kwargs: KiteTicker(*args, url=ws_url, **kwargs)
        )
        self._clock = clock
        self._feed = KiteFeed(store, market_data)
        self._links: dict[str, _Link] = {}
        self._prices: _Link | None = None  # the live account whose ticker carries prices
        self._instruments_day: str | None = None  # the day the instrument list is from
        self.login = KiteDailyLogin(
            apps,
            token_dir,
            on_session=self.go_live,
            http_factory=lambda key, **kw: self._http_factory(key, **kw),
        )
        market_data.on_demand(self._on_demand)

    # --- set-up -----------------------------------------------------------------------------

    def add_account(self, account: BrokerAccount) -> KiteBroker:
        """The account's broker, usable at once: orders are refused until it goes live."""
        app = self._apps(account)
        http = http_for(self._http_factory, app)  # its own IP when it has one (#372)
        broker = KiteBroker(http, kite_user_id=account.broker_client_id, account_id=account.id)
        prices = MarketData()
        link = _Link(account, app, http, broker, prices, KiteFeed(self._store, prices))
        prices.on_demand(lambda instrument_id, link=link: self._on_own_demand(link, instrument_id))
        self._links[account.id] = link
        return broker

    def prices_of(self, account_id: str) -> MarketData:
        """This account's own prices (#395), fed only by its own ticker."""
        return self._links[account_id].prices

    def status(self, account: BrokerAccount) -> str | None:
        link = self._links.get(account.id)
        if link is None:
            return None
        return LIVE if link.live else LOGIN_NEEDED

    @property
    def prices_from(self) -> str | None:
        """The account whose connection carries prices (None: no prices yet)."""
        return self._prices.account.id if self._prices else None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """A read request (e.g. historical candles) through the account that carries prices."""
        link = self._prices
        if link is None or not link.live:
            raise KiteAuthError("no Kite account is logged in", "TokenException")
        return await link.http.request(method, path, **kwargs)

    async def start(self) -> None:
        """Yesterday's instruments (so screens can search), then any saved logins."""
        if self._cache.exists():
            report = load_kite_instruments(self._cache.read_text(encoding="utf-8"), self._store)
            log.info("instruments from cache: %d (refreshed at the first login)", report.loaded)
        now = self._clock()
        for link in list(self._links.values()):
            session = token_store.load(self.login.token_file(link.account), now)
            if session is not None:
                await self.go_live(link.account, session)

    # --- going live and back ----------------------------------------------------------------

    async def go_live(self, account: BrokerAccount, session: KiteSession) -> None:
        link = self._links.get(account.id)
        if link is None:
            return
        await self._stop_ticker(link)
        link.http.access_token = session.access_token
        link.expires = token_store.expires_at(self._clock())
        log.info("kite account %s live until %s", account.id, link.expires.isoformat())
        await self._refresh_instruments(link)
        link.ticker = self._ticker_factory(
            link.app.api_key,
            session.access_token,
            lambda ticks, link=link: self._on_ticks(link, ticks),
            link.broker.on_order_message,
            on_connect=lambda link=link: self._connected(link),
        )
        link.task = asyncio.create_task(link.ticker.run(), name=f"kite-ticker-{account.id}")
        link.task.add_done_callback(lambda task, link=link: self._ticker_ended(link, task))
        own = self._tokens(link.prices.watched(), link.feed)  # what its screens already watch
        if own:
            await self._subscribe(link, own)
        if self._prices is None or not self._prices.live:
            await self._take_prices(link)

    def log_out(self, link: _Link, why: str) -> None:
        """The token is gone (expired or refused): orders refused, ticker stopped."""
        if not link.live:
            return
        log.warning("kite account %s logged out: %s", link.account.id, why)
        link.http.access_token = None
        link.expires = None
        if link.ticker is not None:
            link.ticker.stop()
        if self._prices is link:
            self._prices = None
            for other in self._links.values():
                if other.live:
                    asyncio.ensure_future(self._take_prices(other))
                    break

    async def start_and_run(self) -> None:
        await self.start()
        await self.run()

    async def run(self) -> None:
        """Log accounts out when their token expires (6 AM India time)."""
        while True:
            self.expire_due()
            await asyncio.sleep(15)

    def expire_due(self) -> None:
        now = self._clock()
        for link in self._links.values():
            if link.expires is not None and now >= link.expires:
                self.log_out(link, "the daily Kite login expired (6 AM)")

    async def aclose(self) -> None:
        for link in self._links.values():
            await self._stop_ticker(link)
            with contextlib.suppress(Exception):
                await link.http.aclose()

    async def refresh_instruments(self) -> None:
        """The morning job: today's instrument list, if an account is live (else at login)."""
        for link in self._links.values():
            if link.live:
                await self._refresh_instruments(link)
                return

    @property
    def any_live(self) -> bool:
        return any(link.live for link in self._links.values())

    # --- prices ------------------------------------------------------------------------------

    async def _take_prices(self, link: _Link) -> None:
        """This account's ticker carries prices from now on: subscribe everything watched."""
        self._prices = link
        tokens = self._tokens(self._md.watched())
        if tokens and link.ticker is not None:
            await self._subscribe(link, tokens)
        log.info("prices from kite account %s (%d contracts)", link.account.id, len(tokens))

    def _on_ticks(self, link: _Link, ticks: list) -> None:
        link.feed.on_ticks(ticks)  # its own prices, always (#395)
        if link is self._prices:
            self._feed.on_ticks(ticks)

    def _on_own_demand(self, link: _Link, instrument_id: InstrumentId) -> None:
        """Its own screens or checks want a contract: its own ticker subscribes it."""
        if link.ticker is None:
            return  # subscribed when it goes live
        tokens = self._tokens([instrument_id], link.feed)
        if tokens:
            asyncio.ensure_future(self._subscribe(link, tokens))

    def _on_demand(self, instrument_id: InstrumentId) -> None:
        link = self._prices
        if link is None or link.ticker is None:
            return  # subscribed when prices start
        tokens = self._tokens([instrument_id])
        if tokens:
            asyncio.ensure_future(self._subscribe(link, tokens))

    def _tokens(self, instrument_ids, feed: KiteFeed | None = None) -> list[int]:
        tokens = []
        for instrument_id in instrument_ids:
            try:
                tokens.extend((feed or self._feed).tokens_for([instrument_id]))
            except KeyError:
                pass  # not a Kite contract (e.g. listed only by another broker)
        return tokens

    async def _subscribe(self, link: _Link, tokens: list[int]) -> None:
        try:
            await link.ticker.subscribe(tokens, mode="full")
        except ValueError as exc:  # over Kite's per-connection limit
            log.error("kite subscription refused: %s", exc)
        except Exception:
            log.exception("kite subscription failed")

    # --- inside ----------------------------------------------------------------------------

    async def _refresh_instruments(self, link: _Link) -> None:
        """Today's instrument list, once a day, from the first account that goes live."""
        today = self._clock().astimezone(token_store.IST).date().isoformat()
        if self._instruments_day == today:
            return
        try:
            text = await link.http.get_csv("/instruments")
        except Exception as exc:
            log.error("kite instrument list not refreshed: %r (using the cached one)", exc)
            return
        report = load_kite_instruments(text, self._store)
        freeze_heap()  # today's ~90,000 contracts: out of the garbage collector's way
        self._instruments_day = today
        log.info("instruments from kite: %d loaded, %d skipped", report.loaded, report.skipped)
        self._cache.parent.mkdir(parents=True, exist_ok=True)
        part = self._cache.with_suffix(".part")
        part.write_text(text, encoding="utf-8")
        os.replace(part, self._cache)  # never a half-written cache

    def _connected(self, link: _Link) -> None:
        """A (re)connection: order updates may have been missed while it was down."""
        if self._on_reconnect is not None:
            self._on_reconnect(link.account.id)

    def _ticker_ended(self, link: _Link, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc: Any = task.exception()
        if exc is not None and link.ticker is not None and task is link.task:
            self.log_out(link, f"Kite refused the connection ({exc})")

    async def _stop_ticker(self, link: _Link) -> None:
        if link.ticker is not None:
            link.ticker.stop()
        if link.task is not None:
            link.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await link.task
        link.ticker, link.task = None, None
