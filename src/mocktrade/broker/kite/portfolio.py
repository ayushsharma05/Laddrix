"""Kite's holdings, margins and tradebook in MockTrade's terms.

    GET /portfolio/holdings   delivery holdings: quantity (settled), t1_quantity, used_quantity,
                              average_price, last_price, close_price
    GET /user/margins         {"equity": {...}, "commodity": {...}}: net (what can be used),
                              available.{cash, opening_balance, collateral, ...},
                              utilised.{debits, span, exposure, option_premium, ...}
    GET /trades               today's fills: trade_id, order_id, transaction_type, product,
                              quantity, average_price, fill_timestamp

Kite's figures are shown as Kite gives them (rupees -> paise): net is Kite's "available",
debits Kite's "used". MockTrade doesn't add them up again: the signs of Kite's M2M lines
are Kite's business, and a recomputed total that disagrees with Kite's would mislead.
A row we can't read is skipped (logged), never guessed.
"""

import logging
from datetime import datetime
from typing import Any

from mocktrade.broker.kite.orders import to_paise
from mocktrade.domain.orders import Product, Side
from mocktrade.domain.portfolio import Funds, FundsLine, Holding, Trade
from mocktrade.options.model import IST

log = logging.getLogger(__name__)

# Kite's field -> the label screens show, in the order shown.
AVAILABLE = {
    "opening_balance": "Opening balance",
    "live_balance": "Live balance",
    "cash": "Cash",
    "intraday_payin": "Pay-in today",
    "collateral": "Collateral",
    "adhoc_margin": "Ad hoc margin",
}
UTILISED = {
    "span": "SPAN",
    "exposure": "Exposure",
    "option_premium": "Options premium",
    "delivery": "Delivery margin",
    "holding_sales": "Holdings sold",
    "m2m_realised": "Realised M2M",
    "m2m_unrealised": "Unrealised M2M",
    "payout": "Payout",
    "turnover": "Turnover",
}


def parse_holding(row: dict[str, Any]) -> Holding | None:
    try:
        ltp = to_paise(row.get("last_price"))
        close = to_paise(row.get("close_price"))
        return Holding(
            instrument_id=f"{row['exchange']}:{row['tradingsymbol']}",
            qty=int(row.get("quantity") or 0),
            t1_qty=int(row.get("t1_quantity") or 0),
            used_qty=int(row.get("used_quantity") or 0),
            avg_price_paise=to_paise(row.get("average_price")),
            ltp_paise=ltp or None,
            close_paise=close or None,
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        log.warning("unreadable Kite holding: %r", row)
        return None


def parse_margins(data: dict[str, Any]) -> list[Funds]:
    result = []
    for segment in ("equity", "commodity"):
        row = data.get(segment)
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        available = row.get("available") or {}
        utilised = row.get("utilised") or {}
        result.append(
            Funds(
                segment=segment,
                available_paise=to_paise(row.get("net")),
                used_paise=to_paise(utilised.get("debits")),
                opening_paise=to_paise(available.get("opening_balance")),
                collateral_paise=to_paise(available.get("collateral")),
                available_lines=_lines(available, AVAILABLE),
                used_lines=_lines(utilised, UTILISED),
            )
        )
    return result


def parse_trade(row: dict[str, Any]) -> Trade | None:
    try:
        return Trade(
            trade_id=str(row["trade_id"]),
            broker_order_id=str(row["order_id"]),
            instrument_id=f"{row['exchange']}:{row['tradingsymbol']}",
            side=Side(row["transaction_type"]),
            product=Product(row["product"]),
            qty=int(row["quantity"]),
            price_paise=to_paise(row["average_price"]),
            ts=_ts(row.get("fill_timestamp") or row.get("exchange_timestamp")),
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        log.warning("unreadable Kite trade: %r", row)
        return None


def _lines(values: dict[str, Any], labels: dict[str, str]) -> tuple[FundsLine, ...]:
    return tuple(FundsLine(label, to_paise(values[key])) for key, label in labels.items()
                 if key in values)  # fmt: skip


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None
