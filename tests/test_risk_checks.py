"""Pre-trade risk checks and the kill switch."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from mocktrade.broker.base import BrokerError
from mocktrade.broker.fake import FakeBroker
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
)
from mocktrade.domain.ticks import Tick
from mocktrade.orders.gate import Gates
from mocktrade.orders.manager import OrderManager
from mocktrade.orders.model import ClientIds, OrderView, Pending
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.checks import RiskGate
from mocktrade.risk.kill_switch import KillSwitch, stop_trading
from mocktrade.risk.limits import RiskLimits

FUT = "NFO:NIFTY26OCTFUT"
CALL = "NFO:NIFTY26OCT25000CE"
REL = "NSE:RELIANCE"
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)

CALL_OPTION = Instrument(
    Exchange.NFO,
    "NIFTY26OCT25000CE",
    InstrumentKind.CALL,
    tick_paise=5,
    lot_size=75,
    expiry=date(2026, 10, 27),
    strike_paise=2500000,
    underlying="NIFTY",
)


class World:
    """Everything the risk gate reads: prices, positions, working orders, P&L, switches."""

    def __init__(self, store):
        self.store = store
        self.ticks = {
            FUT: Tick(FUT, 2501000),
            CALL: Tick(CALL, 12000),  # ₹120.00
            REL: Tick(REL, 140000),  # ₹1,400.00
        }
        self.positions: dict[str, int] = {}
        self.by_product: dict | None = None  # (instrument, product) -> qty, when a test sets it
        self.working: list[OrderView] = []
        self.pnl = 0
        self.firm = KillSwitch("firm")
        self.account = KillSwitch("account acc-1")
        self.now = NOW

    def gate(self, **limits) -> RiskGate:
        return RiskGate(
            RiskLimits(**limits),
            instruments=self.store.get,
            prices=self.ticks.get,
            position=lambda i: self.positions.get(i, 0),
            working=lambda: list(self.working),
            day_pnl_paise=lambda: self.pnl,
            switches=(self.firm, self.account),
            clock=lambda: self.now,
            product_position=(lambda i, p: self.by_product.get((i, p), 0))
            if self.by_product is not None else None,
        )  # fmt: skip


@pytest.fixture
def store(nifty_fut, reliance):
    s = InstrumentStore()
    for instrument in (nifty_fut, reliance, CALL_OPTION):
        s.add(instrument)
    return s


@pytest.fixture
def world(store):
    return World(store)


def req(instrument_id=FUT, side=Side.BUY, qty=75, price=2501000, **changes) -> OrderRequest:
    base = OrderRequest(
        client_order_id="NEW1",
        account_id="acc-1",
        user_id="u-ayush",
        instrument_id=instrument_id,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT,
        product=Product.CNC if instrument_id == REL else Product.NRML,
        price_paise=price,
    )
    return replace(base, **changes)


def working(request: OrderRequest, cid: str, filled=0, created=NOW - timedelta(minutes=5)):
    return OrderView(
        request=replace(request, client_order_id=cid),
        broker_order_id="B" + cid,
        status=OrderStatus.OPEN if filled == 0 else OrderStatus.PARTIAL,
        filled_qty=filled,
        avg_price_paise=0,
        reason="",
        pending=Pending.NONE,
        created_at=created,
        updated_at=created,
    )


async def check(gate, request):
    return await gate.admit_new(request)


# --- a normal order passes ---


async def test_a_sensible_order_passes(world):
    assert await check(world.gate(), req()) is None
    assert await check(world.gate(), req(REL, qty=10, price=140000)) is None
    assert await check(world.gate(), req(CALL, price=12000)) is None


# --- kill switch ---


async def test_kill_switch_blocks_new_orders_and_modifies_but_never_cancels(world):
    gate = world.gate()
    assert world.account.engage("too many rejects", by="u-ayush")
    reason = await check(gate, req())
    assert reason == "trading is stopped (account acc-1 kill switch: too many rejects)"
    order = working(req(), "W1")
    assert "stopped" in await gate.admit_modify(order, replace(order.request, qty=150))
    assert await gate.admit_cancel(order) is None
    assert world.account.reset(by="u-ayush")
    assert await check(gate, req()) is None


async def test_firm_switch_covers_every_account(world):
    world.firm.engage("exchange outage", by="admin")
    assert "firm kill switch: exchange outage" in await check(world.gate(), req())


def test_kill_switch_keeps_the_first_reason_and_records_who(world):
    times = iter([NOW, NOW + timedelta(seconds=5), NOW + timedelta(seconds=9)])
    switch = KillSwitch("account acc-1", clock=lambda: next(times))
    heard = []
    switch.on_change(lambda name, state: heard.append((name, state.engaged, state.by)))
    switch.on_change(lambda name, state: 1 / 0)  # a broken listener changes nothing
    assert switch.engage("daily loss", by="auto: daily loss")
    assert not switch.engage("second reason", by="u-ayush")
    assert (switch.state.reason, switch.state.at) == ("daily loss", NOW)
    assert switch.reset(by="u-ayush") and not switch.reset(by="u-ayush")
    assert heard == [
        ("account acc-1", True, "auto: daily loss"),
        ("account acc-1", False, "u-ayush"),
    ]


async def test_stop_trading_engages_first_then_cancels_everything(store, world):
    broker = FakeBroker()
    gate = world.gate()
    om = OrderManager("acc-1", broker, store.get, ids=ClientIds("MT"), gate=gate)
    world.working = om.working  # the gate reads the manager's live working orders
    gate._working = om.working
    first = await om.submit(req(client_order_id=""))
    second = await om.submit(req(client_order_id="", price=2500990))
    cancels_at_engage = []
    world.account.on_change(lambda name, state: cancels_at_engage.append(list(broker.cancelled)))
    original = broker.cancel

    async def refuse_second(broker_order_id):
        if broker_order_id == second.broker_order_id:
            raise BrokerError("order is being processed")
        await original(broker_order_id)

    broker.cancel = refuse_second
    failures = await stop_trading(world.account, [om], "user pressed STOP", by="u-ayush")
    assert cancels_at_engage == [[]]  # engaged before any cancel went out
    assert broker.cancelled == [first.broker_order_id]
    assert failures == {"acc-1": [(second.client_order_id, "order is being processed")]}
    blocked = await om.submit(req(client_order_id="", price=2500980))
    assert blocked.status is OrderStatus.REJECTED and "STOP" in blocked.reason


# --- instruments and products ---


@pytest.mark.parametrize(
    "limits, request_, message",
    [
        ({"allowed_exchanges": frozenset({Exchange.NSE})}, req(), "NFO is not enabled"),
        (
            {"allowed_kinds": frozenset({InstrumentKind.EQUITY})},
            req(),
            "future trading is not enabled",
        ),
        ({"blocked_instruments": frozenset({FUT})}, req(), "NIFTY26OCTFUT is blocked"),
        ({}, req(product=Product.CNC), "CNC can't be used for NIFTY26OCTFUT (use MIS, NRML)"),
        ({}, req(REL, qty=1, price=140000, product=Product.NRML), "use CNC, MIS"),
    ],
)
async def test_instrument_and_product_rules(world, limits, request_, message):
    assert message in await check(world.gate(**limits), request_)


async def test_selling_options_to_open_is_off_by_default(world):
    sell = req(CALL, side=Side.SELL, price=12000)
    assert "selling options to open" in await check(world.gate(), sell)
    assert await check(world.gate(allow_option_selling=True), sell) is None


async def test_selling_options_you_own_is_closing_and_allowed(world):
    world.positions[CALL] = 150
    sell = req(CALL, side=Side.SELL, qty=150, price=12000)
    assert await check(world.gate(), sell) is None
    world.working = [working(req(CALL, side=Side.SELL, price=12100), "W1")]  # 75 already selling
    assert "selling options to open" in await check(world.gate(), sell)


# --- size and value ---


async def test_too_many_lots_in_one_order(world):
    assert await check(world.gate(max_order_value_paise=10**12), req(qty=750)) is None
    reason = await check(world.gate(max_order_value_paise=10**12), req(qty=825))
    assert reason == "11 lots (825) is more than the 10-lot limit per order"


async def test_order_value_limit(world):
    reason = await check(world.gate(), req(qty=225))  # 3 lots × ₹25,010 × 75 = ₹56.27 lakh
    assert reason == "order value ₹56,27,250.00 is above the ₹50,00,000.00 limit per order"
    assert await check(world.gate(), req(qty=150)) is None


async def test_market_orders_are_valued_at_their_protected_worst_price(world):
    market = req(qty=150, price=None, order_type=OrderType.MARKET, market_protection=Decimal(2))
    # 150 × 25,510.20 (LTP + 2%) = ₹38.27 lakh: fine; with a ₹38 lakh limit it isn't
    assert await check(world.gate(), market) is None
    assert "order value" in await check(world.gate(max_order_value_paise=38_00_000_00), market)


# --- fat finger ---


@pytest.mark.parametrize(
    "request_, ok",
    [
        (req(price=2620000), True),  # +4.76%
        (req(price=2630000), False),  # +5.16%
        (req(price=2370000), False),  # -5.24%: a typo either way
        (req(CALL, price=16800), True),  # option +40%
        (req(CALL, price=17000), False),  # option +41.7%
    ],
)
async def test_price_must_be_near_the_last_price(world, request_, ok):
    reason = await check(world.gate(), request_)
    assert (reason is None) is ok, reason


async def test_fat_finger_message_is_specific(world):
    reason = await check(world.gate(), req(price=2701000))
    assert reason == (
        "price ₹27,010.00 is 8.0% from the last price ₹25,010.00 (limit 5%): check for a typo"
    )


async def test_trigger_price_is_checked_too(world):
    stop = req(order_type=OrderType.SL, price=2502000, trigger_paise=2800000)
    assert (await check(world.gate(), stop)).startswith("trigger ₹28,000.00")


async def test_without_a_live_price(world):
    del world.ticks[FUT]
    assert "no live price" in await check(world.gate(), req())
    assert await check(world.gate(require_market_price=False), req()) is None
    market = req(price=None, order_type=OrderType.MARKET, market_protection=Decimal(2))
    assert "market orders need one" in await check(world.gate(require_market_price=False), market)


# --- position ---


async def test_position_limit_counts_working_orders(world):
    gate = world.gate(max_position_lots=4)  # ±300
    world.positions[FUT] = 150
    assert await check(gate, req(qty=150)) is None  # up to 300: fine
    world.working = [working(req(price=2500990), "W1")]  # 75 more already bidding
    reason = await check(gate, req(qty=150))
    assert reason == (
        "NIFTY26OCTFUT: buying 2 lots (150) would make your position up to +375 (limit ±300)"
    )


async def test_the_order_being_checked_never_counts_itself(world):
    gate = world.gate(max_position_lots=4)
    world.positions[FUT] = 150
    world.working = [working(req(qty=150), "NEW1")]  # the manager already lists it as PENDING
    assert await check(gate, req(qty=150)) is None


async def test_reducing_a_position_beyond_the_limit_is_always_allowed(world):
    gate = world.gate(max_position_lots=4)
    world.positions[FUT] = -450  # short beyond the limit (e.g. the limit was lowered)
    assert await check(gate, req(side=Side.BUY, qty=75)) is None  # -375: still beyond, but less
    assert "position up to -525" in await check(gate, req(side=Side.SELL, qty=75))


async def test_partly_filled_orders_count_only_what_is_left(world):
    gate = world.gate(max_position_lots=4)
    world.positions[FUT] = 75  # the 75 filled of W1
    world.working = [working(req(qty=150), "W1", filled=75)]  # 75 left
    assert await check(gate, req(qty=150)) is None  # 75 + 75 + 150 = 300


# --- daily loss ---


async def test_past_the_daily_loss_only_closing_trades_are_allowed(world):
    gate = world.gate(max_order_value_paise=10**12)
    world.positions[FUT] = 150
    world.pnl = -19_999_00
    assert await check(gate, req()) is None
    world.pnl = -20_000_00
    reason = await check(gate, req())
    assert reason == (
        "daily loss limit reached (₹20,000.00 lost, limit ₹20,000.00): "
        "only closing trades are allowed"
    )
    assert await check(gate, req(side=Side.SELL, qty=150, price=2501000)) is None  # close
    assert "daily loss" in await check(gate, req(side=Side.SELL, qty=225))  # would flip short
    world.positions[FUT] = 0
    assert "daily loss" in await check(gate, req(side=Side.SELL))  # opening from flat


async def test_only_the_same_products_position_can_be_closed(world):
    """#258: long 2 lots NRML. Past the loss limit a MIS sell doesn't close it (at the broker it
    opens a MIS short); an NRML sell does. Working NRML sells count against what's left."""
    world.by_product = {(FUT, Product.NRML): 150}
    world.positions[FUT] = 150
    gate = world.gate(max_order_value_paise=10**12)
    world.pnl = -20_000_00
    assert "daily loss" in await check(gate, req(side=Side.SELL, qty=75, product=Product.MIS))
    assert await check(gate, req(side=Side.SELL, qty=75, product=Product.NRML)) is None
    world.working = [working(req(side=Side.SELL, qty=75, product=Product.NRML), "W1"),
                     working(req(side=Side.SELL, qty=75, product=Product.MIS), "W2")]  # fmt: skip
    assert await check(gate, req(side=Side.SELL, qty=75, product=Product.NRML)) is None
    assert "daily loss" in await check(gate, req(side=Side.SELL, qty=150, product=Product.NRML))
    gate.off_reason = "the account is turned off by an admin"  # the same rule when turned off
    world.pnl = 0
    assert "turned off" in await check(gate, req(side=Side.SELL, qty=75, product=Product.MIS))


# --- open orders and double clicks ---


async def test_open_order_limit(world):
    world.working = [working(req(REL, qty=1, price=139000), f"W{i}") for i in range(3)]
    assert "3 orders are already working (limit 3)" in await check(
        world.gate(max_open_orders=3), req()
    )


async def test_identical_manual_order_moments_later_is_a_double_click(world):
    world.working = [working(req(), "W1", created=NOW - timedelta(seconds=0.3))]
    reason = await check(world.gate(), req())
    assert reason.startswith("an identical order was placed 0.3 s ago: looks like a double click")
    assert await check(world.gate(), req(price=2500990)) is None  # not identical
    assert await check(world.gate(), req(algo_id="ALG1")) is None  # algos may repeat
    world.now = NOW + timedelta(seconds=0.8)
    assert await check(world.gate(), req()) is None  # a second later: on purpose


async def test_a_refused_order_never_blocks_its_retry(world, store):
    """Throttle says 'try again in 0.3 s': the retry must not look like a double click."""
    broker = FakeBroker()
    gate = world.gate()

    class Full:
        calls = 0

        async def admit_new(self, request):
            Full.calls += 1
            return "rate limit; try again in 0.3 s" if Full.calls == 1 else None

        async def admit_modify(self, order, new):
            return None

        async def admit_cancel(self, order):
            return None

    om = OrderManager(
        "acc-1",
        broker,
        store.get,
        ids=ClientIds("MT"),
        gate=Gates([gate, Full()]),
        clock=lambda: world.now,
    )
    gate._working = om.working
    assert (await om.submit(req(client_order_id=""))).status is OrderStatus.REJECTED
    assert (await om.submit(req(client_order_id=""))).status is OrderStatus.PENDING


# --- modifications ---


async def test_modify_checks_the_new_values(world):
    gate = world.gate(max_position_lots=4, max_order_value_paise=10**12)
    order = working(req(qty=150), "W1")
    world.working = [order]
    assert "typo" in await gate.admit_modify(order, replace(order.request, price_paise=2800000))
    bigger = replace(order.request, qty=375)
    assert "position up to +375" in await gate.admit_modify(order, bigger)
    world.positions[FUT] = 300  # over the limit after fills elsewhere
    smaller = replace(order.request, qty=75)
    assert await gate.admit_modify(order, smaller) is None  # shrinking is always fine


# --- limits ---


@pytest.mark.parametrize(
    "bad",
    [
        {"max_order_lots": 0},
        {"max_position_lots": -1},
        {"max_open_orders": 0},
        {"max_daily_loss_paise": 0},
        {"price_band_pct": {InstrumentKind.FUTURE: Decimal(0)}},
    ],
)
def test_limits_must_be_positive(bad):
    with pytest.raises(ValueError):
        RiskLimits(**bad)


# --- in the order manager ---


async def test_risk_rejection_reaches_the_order_view(world, store):
    gate = world.gate()
    om = OrderManager("acc-1", FakeBroker(), store.get, ids=ClientIds("MT"), gate=Gates([gate]))
    gate._working = om.working
    view = await om.submit(req(client_order_id="", price=2701000))
    assert view.status is OrderStatus.REJECTED and "check for a typo" in view.reason


@pytest.mark.parametrize(
    "paise, shown",
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (99999, "₹999.99"),
        (100000, "₹1,000.00"),
        (2000000, "₹20,000.00"),
        (562725000, "₹56,27,250.00"),
        (1234567890_12, "₹1,23,45,67,890.12"),
        (-2000000, "-₹20,000.00"),
    ],
)
def test_rupees_are_shown_in_lakh_and_crore_grouping(paise, shown):
    from mocktrade.domain.money import format_rupees

    assert format_rupees(paise) == shown


async def test_matching_an_algos_working_order_is_not_a_double_click(world):
    algo_order = working(req(algo_id="ALG1"), "W1", created=NOW - timedelta(seconds=0.2))
    world.working = [algo_order]
    assert await check(world.gate(), req()) is None


async def test_stop_market_orders_are_valued_from_their_trigger(world):
    stop = req(
        qty=150,
        price=None,
        order_type=OrderType.SL_M,
        trigger_paise=2600000,
        market_protection=Decimal(2),
    )
    # from the trigger: 150 × 26,520 = ₹39.78 lakh; from the LTP it would be ₹38.27 lakh
    assert "order value ₹39,78,000.00" in await check(
        world.gate(max_order_value_paise=39_00_000_00), stop
    )
