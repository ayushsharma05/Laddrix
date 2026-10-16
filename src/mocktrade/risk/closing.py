"""Closing a position through the normal order path (risk, margin, throttle, audit).

Used by the MTM watch and the square-off assistant: a market order with the broker's
automatic protection for the open quantity, split so no piece is bigger than the account
allows per order (risk limits) or the exchange per order (the instrument's freeze quantity,
when known). An exit never needs margin (risk/margin.py) and risk allows closing trades even
past the daily loss limit, so these go through when it matters most.
"""

from decimal import Decimal

from mocktrade.domain.instruments import Instrument
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.orders.model import OrderView


async def close_position(
    orders,  # OrderManager
    instrument: Instrument,
    product: Product,
    qty: int,  # the position: + long (sell to close), − short (buy to close)
    *,
    user_id: str,
    max_lots: int,
    basket_id: str,  # one id for the whole close: its pieces are meant to be identical
) -> list[OrderView]:
    if qty == 0:
        return []
    side = Side.SELL if qty > 0 else Side.BUY
    left = abs(qty)
    piece = max(instrument.lot_size, max_lots * instrument.lot_size)
    freeze = getattr(instrument, "freeze_qty", None)
    if freeze:
        piece = min(piece, freeze - freeze % instrument.lot_size or instrument.lot_size)
    sent = []
    while left > 0:
        size = min(left, piece)
        request = OrderRequest("", orders.account_id, user_id, instrument.id, side, size,
                               OrderType.MARKET, product, market_protection=Decimal(-1),
                               basket_id=basket_id)  # fmt: skip
        sent.append(await orders.submit(request))
        left -= size
    return sent
