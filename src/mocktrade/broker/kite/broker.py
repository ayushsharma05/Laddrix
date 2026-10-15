"""KiteBroker: real orders through Kite Connect, behind the same Broker interface.

    place/modify/cancel ─► KiteHttp (REST) ─► Kite ─► exchange
    order updates       ◄─ KiteTicker "order" text messages ◄─ Kite
    orders()/positions() ─► GET /orders, /portfolio/positions (for reconcile)

The most important rule here is which errors mean "not placed" and which mean
"we don't know":

    clear refusal (BrokerError)          unknown outcome (KiteUnknownOutcome)
    ─────────────────────────────        ────────────────────────────────────
    Input/Order/Margin/Holding/User      NetworkException, DataException,
    TokenException, HTTP 403, 429        GeneralException, HTTP 5xx, timeouts,
                                         dropped connections

An unknown outcome may have reached the exchange, so the order manager marks it
UNCONFIRMED and reconciles from Kite's order book. Nothing here ever retries a
place by itself: a retry could create a second real order.

Kite rules that matter (checked 14 Oct 2026): order endpoints accept only the
registered static IP; tag is alphanumeric, at most 20 characters (our client id);
market_protection is required for MARKET and SL-M (-1 or 0-100, never 0).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mocktrade.broker.base import BasketMargin, BrokerError, BrokerPosition
from mocktrade.broker.kite.gtt import gtt_params, parse_gtt
from mocktrade.broker.kite.http import KiteAuthError, KiteError, KiteHttp, KiteRateLimited
from mocktrade.broker.kite.orders import (
    margin_params,
    modify_params,
    place_params,
    to_paise,
    to_update,
)
from mocktrade.broker.rules import order_spec
from mocktrade.domain.gtt import GttRequest, GttSpec, GttView, standard_gtt_spec
from mocktrade.domain.instruments import Instrument
from mocktrade.domain.order_spec import OrderSpec, products_for
from mocktrade.domain.orders import OrderRequest, OrderUpdate, Product

log = logging.getLogger(__name__)

# Kite errors after which the order was certainly not placed or changed.
CLEAR_REFUSALS = frozenset(
    {"InputException", "OrderException", "MarginException", "HoldingException", "UserException"}
)

REGULAR = "regular"
AMO = "amo"  # after-market order: queued for the next session


class KiteUnknownOutcome(Exception):
    """Kite may or may not have acted. Check the order book; never retry blindly."""


class KiteBroker:
    def __init__(
        self,
        http: KiteHttp,
        *,
        kite_user_id: str,  # the Zerodha client id this account trades as, e.g. "AB1234"
        name: str = "kite",
        variety: str = REGULAR,
        account_id: str = "",
        sleep=asyncio.sleep,
    ) -> None:
        self.name = name
        self.kite_user_id = kite_user_id
        self.account_id = account_id  # MockTrade's account, stamped on what we read back
        self.variety = variety  # set to AMO to place after-market orders
        self._http = http
        self._sleep = sleep
        self._queue: asyncio.Queue[OrderUpdate] = asyncio.Queue()
        self._varieties: dict[str, str] = {}  # broker order id -> variety (modify/cancel URLs)
        self.ignored_messages = 0

    # --- the Broker interface ------------------------------------------------------------------

    def order_spec(self, instrument: Instrument) -> OrderSpec:
        """The order types, validities and products Kite offers on this instrument."""
        return order_spec(instrument)

    async def place(self, request: OrderRequest) -> str:
        variety = self.variety
        data = await self._call(
            "POST", f"/orders/{variety}", place_params(request), what="place order"
        )
        order_id = str(data["order_id"])
        self._varieties[order_id] = variety
        return order_id

    async def modify(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        price_paise: int | None = None,
        trigger_paise: int | None = None,
    ) -> None:
        variety = self._varieties.get(broker_order_id, self.variety)
        params = modify_params(qty, price_paise, trigger_paise)
        await self._call("PUT", f"/orders/{variety}/{broker_order_id}", params, what="modify order")

    async def cancel(self, broker_order_id: str) -> None:
        variety = self._varieties.get(broker_order_id, self.variety)
        await self._call("DELETE", f"/orders/{variety}/{broker_order_id}", None, what="cancel")

    async def updates(self) -> AsyncIterator[OrderUpdate]:
        while True:
            yield await self._queue.get()

    async def orders(self) -> list[OrderUpdate]:
        """Today's orders from Kite's order book (the truth, for reconcile)."""
        rows = await self._read("/orders")
        updates = []
        for row in rows or []:
            self._varieties.setdefault(str(row["order_id"]), row.get("variety") or REGULAR)
            update = to_update(row)
            if update is not None:
                updates.append(update)
        return updates

    async def positions(self) -> list[BrokerPosition]:
        """Kite's net positions (for reconciling ours)."""
        data = await self._read("/portfolio/positions")
        result = []
        for row in (data or {}).get("net", []):
            result.append(
                BrokerPosition(
                    instrument_id=f"{row['exchange']}:{row['tradingsymbol']}",
                    qty=int(row.get("quantity") or 0),
                    avg_price_paise=to_paise(row.get("average_price")),
                    overnight_qty=int(row.get("overnight_quantity") or 0),
                    close_paise=to_paise(row.get("close_price")),
                    product=_product(row.get("product")),
                )
            )
        return result

    # --- GTT (good till triggered), held by Kite -------------------------------------------

    def gtt_spec(self, instrument: Instrument) -> GttSpec:
        return standard_gtt_spec(instrument, products_for(instrument))

    async def place_gtt(self, request: GttRequest, ltp_paise: int) -> str:
        data = await self._call(
            "POST", "/gtt/triggers", gtt_params(request, ltp_paise), what="place GTT"
        )
        return str(data["trigger_id"])

    async def modify_gtt(self, gtt_id: str, request: GttRequest, ltp_paise: int) -> None:
        await self._call(
            "PUT", f"/gtt/triggers/{gtt_id}", gtt_params(request, ltp_paise), what="modify GTT"
        )

    async def delete_gtt(self, gtt_id: str) -> None:
        await self._call("DELETE", f"/gtt/triggers/{gtt_id}", None, what="delete GTT")

    async def gtts(self) -> list[GttView]:
        rows = await self._read("/gtt/triggers")
        views = [parse_gtt(row, self.account_id) for row in rows or []]
        return [v for v in views if v is not None]

    async def basket_margin(self, requests: list[OrderRequest]) -> BasketMargin:
        """Kite's margin for the orders together (POST /margins/basket): `final` includes
        the hedge benefit, which is what Kite blocks when they're all in."""
        body = [margin_params(r, self.variety) for r in requests]
        try:
            data = await self._http.request(
                "POST", "/margins/basket", params={"consider_positions": "true"}, json_body=body
            )
        except (KiteError, httpx.HTTPError) as exc:
            message = exc.message if isinstance(exc, KiteError) else repr(exc)
            raise BrokerError(f"Kite margin check: {message}") from None
        return BasketMargin(
            initial_paise=to_paise((data.get("initial") or {}).get("total")),
            final_paise=to_paise((data.get("final") or {}).get("total")),
        )

    # --- order updates from the WebSocket -------------------------------------------------------

    def on_order_message(self, data: dict[str, Any]) -> None:
        """Feed this the `data` of every Kite ticker "order" message (KiteTicker on_order)."""
        if data.get("user_id") and data["user_id"] != self.kite_user_id:
            self.ignored_messages += 1  # not this account's order
            return
        if data.get("order_id") and data.get("variety"):
            self._varieties.setdefault(str(data["order_id"]), data["variety"])
        try:
            update = to_update(data)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("unreadable Kite order message: %r (%s)", data, exc)
            self.ignored_messages += 1
            return
        if update is None:
            self.ignored_messages += 1
            return
        self._queue.put_nowait(update)

    # --- inside ----------------------------------------------------------------------------------

    async def _call(self, method: str, path: str, data: dict | None, *, what: str) -> Any:
        """An order request: clear refusals become BrokerError, the rest unknown outcomes."""
        try:
            return await self._http.request(method, path, data=data)
        except KiteRateLimited as exc:
            raise BrokerError(f"Kite rate limit: {exc.message}") from None
        except KiteAuthError as exc:
            raise BrokerError(f"Kite login needed: {exc.message}") from None
        except KiteError as exc:
            if exc.error_type in CLEAR_REFUSALS and exc.status < 500:
                raise BrokerError(exc.message) from None
            raise KiteUnknownOutcome(f"{what}: {exc.error_type}: {exc.message}") from exc
        except httpx.HTTPError as exc:  # timeouts, refused or dropped connections
            raise KiteUnknownOutcome(f"{what}: {exc!r}") from exc

    async def _read(self, path: str, attempts: int = 3) -> Any:
        """A read-only request: safe to retry, so transient failures are retried."""
        for attempt in range(1, attempts + 1):
            try:
                return await self._http.request("GET", path)
            except (KiteRateLimited, httpx.HTTPError, KiteError) as exc:
                transient = not isinstance(exc, KiteError) or isinstance(exc, KiteRateLimited)
                transient = transient or (isinstance(exc, KiteError) and exc.status >= 500)
                if isinstance(exc, KiteAuthError) or not transient or attempt == attempts:
                    message = exc.message if isinstance(exc, KiteError) else repr(exc)
                    raise BrokerError(f"Kite {path}: {message}") from None
                await self._sleep(0.5 * attempt)
        raise AssertionError("unreachable")


def _product(value: object) -> Product | None:
    try:
        return Product(str(value))
    except ValueError:
        return None
