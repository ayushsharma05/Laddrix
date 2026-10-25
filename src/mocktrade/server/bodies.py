"""The HTTP API's request bodies: unknown fields are refused, so nobody can smuggle in a
user or account id; who is asking always comes from the session."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mocktrade.domain.orders import OrderType, Product, Side, Validity
from mocktrade.research.backtest import (
    MAX_DAYS,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeIn(_Strict):
    code: str = Field(min_length=6, max_length=6)


class PasswordIn(_Strict):
    old: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class LoginIn(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=6)


class OrderIn(_Strict):
    client_order_id: str = Field(default="", pattern=r"^[A-Za-z0-9]{0,20}$")
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0)
    order_type: OrderType
    product: Product
    validity: Validity = Validity.DAY
    price_paise: int | None = Field(default=None, gt=0)
    trigger_paise: int | None = Field(default=None, gt=0)
    market_protection: Decimal | None = None


class ModifyIn(_Strict):
    qty: int | None = Field(default=None, gt=0)
    price_paise: int | None = Field(default=None, gt=0)
    trigger_paise: int | None = Field(default=None, gt=0)


class AlgoIn(_Strict):
    code: str = Field(min_length=1, max_length=32)
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0)
    product: Product = Product.NRML
    params: dict[str, Any] = Field(default_factory=dict)  # checked against the algo's spec


class StrategyBuildIn(_Strict):
    code: str = Field(min_length=1, max_length=40)
    underlying: str = Field(min_length=1, max_length=40)
    expiry: date
    width: int = Field(default=2, ge=1, le=20)
    lots: int = Field(default=1, ge=1, le=100)


class StrategyLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    lots: int = Field(ge=1, le=1000)
    price_paise: int | None = Field(default=None, gt=0)  # entry price; default: live mid


class StrategyIn(_Strict):
    legs: list[StrategyLegIn] = Field(min_length=1, max_length=8)


class BasketLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    lots: int = Field(ge=1, le=1000)
    product: Product = Product.NRML
    price_paise: int | None = Field(default=None, gt=0)
    at: Literal["touch", "mid"] = "touch"


class BasketIn(_Strict):
    legs: list[BasketLegIn] = Field(min_length=1, max_length=10)


class GttLegIn(_Strict):
    trigger_paise: int = Field(gt=0)
    side: Side
    qty: int = Field(gt=0)
    price_paise: int = Field(gt=0)


class GttIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    product: Product
    kind: str = Field(pattern=r"^(single|two-leg)$")
    legs: list[GttLegIn] = Field(min_length=1, max_length=2)


class ChargesIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0, le=10_000_000)
    price_paise: int = Field(gt=0, le=10**12)
    product: Product


class PositionRuleIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    product: Product
    max_loss_paise: int | None = Field(default=None, gt=0, le=10**13)
    max_loss_pct: float | None = Field(default=None, gt=0, le=100)
    auto_close: bool = False


class PortfolioRuleIn(_Strict):
    max_loss_paise: int | None = Field(default=None, gt=0, le=10**13)
    target_paise: int | None = Field(default=None, gt=0, le=10**13)
    auto_close: bool = False


class ReplayIn(_Strict):
    action: str = Field(pattern=r"^(pause|resume|speed|seek)$")
    speed: float | None = None
    at: datetime | None = None  # seek: the time to jump to (ahead only)


class BacktestIn(_Strict):
    days: list[str] = Field(min_length=1, max_length=MAX_DAYS)
    algo: str = Field(min_length=1, max_length=40)
    instrument_id: str = Field(min_length=3, max_length=64)
    side: Side
    qty: int = Field(gt=0, le=10_000_000)
    params: dict[str, Any] = Field(default_factory=dict)
    product: Product = Product.NRML
    start: str = "09:15"
    capital_paise: int = Field(default=10_00_000_00, gt=0, le=10**13)
    name: str = Field(default="", max_length=80)
    candles: bool = False


class AlertIn(_Strict):
    kind: str = Field(min_length=1, max_length=40)
    instrument_id: str = Field(min_length=3, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    repeat: str = "once"
    cooldown_s: int = 300
    note: str = Field(default="", max_length=200)
    webhook: bool = False


class AlertPatch(_Strict):
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    instrument_id: str | None = Field(default=None, min_length=3, max_length=64)
    params: dict[str, Any] | None = None
    repeat: str | None = None
    cooldown_s: int | None = None
    note: str | None = Field(default=None, max_length=200)
    webhook: bool | None = None
    enabled: bool | None = None


class WebhookIn(_Strict):
    url: str = Field(min_length=8, max_length=500)


class CandlesIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    day: date
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class ConfirmIn(_Strict):
    confirm: bool


class MtmRetryIn(_Strict):
    key: str = Field(min_length=1, max_length=100)  # the rule's key, or "portfolio"
    confirm: bool


class RestoreIn(_Strict):
    instrument_id: str = Field(min_length=1, max_length=60)
    confirm: bool


class FlattenIn(_Strict):
    confirm: bool
    instrument_id: str | None = None  # one instrument only (else every open position)
    product: Product | None = None  # with instrument_id: one product only


class AckIn(_Strict):
    note: str  # what was found: it is kept


class AdoptIn(_Strict):
    instrument_id: str
    product: str
    acknowledgement: str  # what the person checked: it goes in the audit trail


class StopIn(_Strict):
    reason: str = Field(min_length=1, max_length=200)


class SettingIn(_Strict):
    value: dict[str, Any] | None


class LayoutIn(_Strict):
    layout: dict[str, Any]  # the screens' own description of their widgets


class SpreadLegIn(_Strict):
    instrument_id: str = Field(min_length=3, max_length=64)
    ratio: int  # the rules (range, sign, one quoting leg) are checked in words: spreads/model.py
    multiplier: int
    quote: bool = False
    payup_ticks: int = 2


class SpreadIn(_Strict):
    name: str = Field(max_length=200)
    legs: list[SpreadLegIn] = Field(max_length=20)
    # how the Autospreader trades it (#425), checked in words against CONTROLS; None: defaults
    # on a new spread, unchanged on an edit
    controls: dict[str, Any] | None = None


class StructureIn(_Strict):
    spread_id: str = Field(min_length=1, max_length=40)  # its legs come from this spread
    name: str | None = Field(default=None, max_length=200)  # None: the spread's name


class AllocationIn(_Strict):
    client_order_id: str = Field(min_length=1, max_length=40)
    qty: int = Field(gt=0, le=10**7)


class MoveIn(AllocationIn):
    to: str = Field(min_length=1, max_length=40)  # the structure it moves to


class ActionIn(_Strict):
    expect: list[str] = Field(max_length=100)  # the preview's lines the trader confirmed


class SpreadOrderIn(_Strict):
    side: Side  # of the spread: BUY buys its + legs and sells its − legs
    lots: int = Field(gt=0, le=1000)  # spread lots
    price_paise: int  # the spread price (may be negative)
    product: Product = Product.NRML
