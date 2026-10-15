"""An account's money and holdings as its broker reports them: holdings, funds, trades.

    Holding  delivery shares kept from earlier days (Kite: /portfolio/holdings)
    Funds    cash and margin, one row per segment (Kite: /user/margins)
    Trade    one fill at the exchange today (Kite: /trades)

Each comes from an optional broker port (broker/base.py); a broker that doesn't offer one
simply doesn't implement it. Money is in paise. The broker's own figures are shown as the
broker gives them; the only arithmetic here is P&L from prices (the same everywhere).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mocktrade.domain.instruments import InstrumentId
from mocktrade.domain.orders import Product, Side


@dataclass(frozen=True)
class Holding:
    """Delivery shares in the demat account. `qty` is settled, `t1_qty` bought but not
    yet settled (T+1); both are owned. `used_qty`: sold today (shown under positions)."""

    instrument_id: InstrumentId
    qty: int
    avg_price_paise: int
    t1_qty: int = 0
    used_qty: int = 0
    ltp_paise: int | None = None  # None until a price is known
    close_paise: int | None = None  # previous close

    @property
    def total_qty(self) -> int:
        return self.qty + self.t1_qty

    @property
    def invested_paise(self) -> int:
        return self.total_qty * self.avg_price_paise

    @property
    def value_paise(self) -> int | None:
        return None if self.ltp_paise is None else self.total_qty * self.ltp_paise

    @property
    def pnl_paise(self) -> int | None:
        value = self.value_paise
        return None if value is None else value - self.invested_paise

    @property
    def day_change_paise(self) -> int | None:
        if self.ltp_paise is None or not self.close_paise:
            return None
        return self.total_qty * (self.ltp_paise - self.close_paise)


@dataclass(frozen=True)
class FundsLine:
    """One labelled figure (the broker names its own lines; screens just draw them)."""

    label: str
    paise: int


@dataclass(frozen=True)
class Funds:
    """Money for one segment ("equity" covers NSE/BSE cash and F&O at Indian brokers)."""

    segment: str
    available_paise: int  # what new orders can use now (Kite: net)
    used_paise: int  # blocked by positions and orders (Kite: utilised.debits)
    opening_paise: int  # cash at the start of the day
    collateral_paise: int = 0  # pledged holdings counted as margin
    available_lines: tuple[FundsLine, ...] = ()
    used_lines: tuple[FundsLine, ...] = ()
    estimate: bool = False  # True when worked out by MockTrade (paper), not the broker's own


@dataclass(frozen=True)
class Trade:
    """One fill at the exchange. `client_order_id` is ours when MockTrade placed the order;
    empty for orders placed elsewhere (Kite web, the phone app)."""

    trade_id: str
    broker_order_id: str
    instrument_id: InstrumentId
    side: Side
    product: Product
    qty: int
    price_paise: int
    ts: datetime | None
    client_order_id: str = ""


# --- what the API sends ----------------------------------------------------------------------


def holding_to_dict(h: Holding) -> dict[str, Any]:
    return {
        "instrument_id": h.instrument_id,
        "qty": h.qty,
        "t1_qty": h.t1_qty,
        "used_qty": h.used_qty,
        "total_qty": h.total_qty,
        "avg_price_paise": h.avg_price_paise,
        "ltp_paise": h.ltp_paise,
        "close_paise": h.close_paise,
        "invested_paise": h.invested_paise,
        "value_paise": h.value_paise,
        "pnl_paise": h.pnl_paise,
        "day_change_paise": h.day_change_paise,
    }


def funds_to_dict(f: Funds) -> dict[str, Any]:
    def lines(items: tuple[FundsLine, ...]) -> list[dict[str, Any]]:
        return [{"label": line.label, "paise": line.paise} for line in items]

    return {
        "segment": f.segment,
        "available_paise": f.available_paise,
        "used_paise": f.used_paise,
        "opening_paise": f.opening_paise,
        "collateral_paise": f.collateral_paise,
        "available_lines": lines(f.available_lines),
        "used_lines": lines(f.used_lines),
        "estimate": f.estimate,
    }


def trade_to_dict(t: Trade) -> dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "broker_order_id": t.broker_order_id,
        "client_order_id": t.client_order_id,
        "instrument_id": t.instrument_id,
        "side": t.side.value,
        "product": t.product.value,
        "qty": t.qty,
        "price_paise": t.price_paise,
        "value_paise": t.qty * t.price_paise,
        "ts": t.ts.isoformat() if t.ts else None,
    }
