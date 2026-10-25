"""Go-live check: place ONE after-market order (AMO) through Kite, then cancel it.

    python -m laddrix.tools.amo_check NSE:SBIN 1 500.00

Run it on the server whose static IP is registered with Kite, outside market hours.
It shows the order and asks you to type YES first. Use a price far from the market so
the order could never fill even if the cancel failed; check the Kite app afterwards.
"""

import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal

from laddrix.broker.base import BrokerError
from laddrix.broker.kite import token_store
from laddrix.broker.kite.broker import AMO, KiteBroker
from laddrix.broker.kite.http import KiteAuthError, KiteHttp
from laddrix.broker.kite.orders import to_kite_rupees
from laddrix.config import KiteSettings, environment
from laddrix.domain.money import to_paise
from laddrix.domain.orders import OrderRequest, OrderType, Product, Side


def build_request(instrument_id: str, qty: str, price_rupees: str) -> OrderRequest:
    """The test order: a delivery (CNC) limit buy, tagged so it's easy to spot."""
    if not instrument_id.startswith(("NSE:", "BSE:")):
        raise ValueError("use a cash-market share (NSE:... or BSE:...) for this check")
    return OrderRequest(
        client_order_id="AMOCHECK1",
        account_id="go-live-check",
        user_id="go-live-check",
        instrument_id=instrument_id,
        side=Side.BUY,
        qty=int(qty),
        order_type=OrderType.LIMIT,
        product=Product.CNC,
        price_paise=to_paise(Decimal(price_rupees)),
    )


async def main(instrument_id: str, qty: str, price: str) -> int:
    request = build_request(instrument_id, qty, price)
    print(
        f"About to place an AMO: BUY {request.qty} {instrument_id} "
        f"@ ₹{to_kite_rupees(request.price_paise)} (CNC, tag {request.client_order_id})"
    )
    if input("Type YES to place it (it will be cancelled straight away): ").strip() != "YES":
        print("Nothing sent.")
        return 1

    settings = KiteSettings.from_env(environment())
    session = token_store.load(settings.token_file, datetime.now(UTC))
    if session is None:
        raise KiteAuthError("no valid Kite login today: run python -m laddrix.tools.login")
    http = KiteHttp(settings.api_key, session.access_token)
    broker = KiteBroker(http, kite_user_id=session.user_id, variety=AMO)
    try:
        order_id = await broker.place(request)
        print(f"placed: Kite order {order_id}")
        await asyncio.sleep(1)
        await broker.cancel(order_id)
        print("cancel sent")
        await asyncio.sleep(1)
        for update in await broker.orders():
            if update.broker_order_id == order_id:
                print(f"final status: {update.status.value} {update.reason}")
        return 0
    except BrokerError as exc:
        print(f"Kite refused: {exc.reason}")
        return 2
    finally:
        await http.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: python -m laddrix.tools.amo_check NSE:SBIN 1 500.00")
    sys.exit(asyncio.run(main(*sys.argv[1:])))
