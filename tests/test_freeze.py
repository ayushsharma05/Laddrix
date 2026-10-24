"""Freeze quantities (#424): the exchange's most per order, from the operator's file, kept by
every order path, and the reason orders are split.

NIFTY's lot is 75 in these tests. With a freeze quantity of 1,800 (24 lots), one order may
carry 1,800; with 1,000 it may carry 975 (13 lots: the most whole lots under the limit).
"""

import asyncio
import logging
from dataclasses import replace
from datetime import date

import pytest

from mocktrade.algos.autospreader import algo_type
from mocktrade.algos.builtin import BUILTIN
from mocktrade.algos.engine import AlgoState
from mocktrade.broker.base import BrokerError
from mocktrade.broker.paper import PaperBroker
from mocktrade.config import ConfigError
from mocktrade.domain.accounts import BrokerAccount, User
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.order_spec import spec_to_dict, standard_spec
from mocktrade.domain.orders import OrderRequest, OrderType, Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.freeze import apply_freeze, parse_freeze
from mocktrade.refdata.store import InstrumentStore, instrument_from_record, instrument_record
from mocktrade.risk.checks import RiskGate
from mocktrade.risk.closing import close_position
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.spreads.model import SpreadLeg
from mocktrade.spreads.store import SpreadBook
from mocktrade.tools.serve import ServeSettings

FUT, NOV = "NFO:NIFTY26OCTFUT", "NFO:NIFTY26NOVFUT"


def store_with(nifty_fut, reliance=None, freeze=None):
    store = InstrumentStore()
    store.add(nifty_fut)
    if reliance is not None:
        store.add(reliance)
    if freeze is not None:
        store.set_freeze(freeze)
    return store


# --- the file -------------------------------------------------------------------------------


def test_the_file_is_read_line_by_line_and_says_what_is_wrong():
    text = """# NSE freeze quantities
underlying,freeze_qty
NIFTY, 1800
banknifty,900
FINNIFTY
MIDCPNIFTY,lots
SENSEX,0
NIFTY,2000
"""
    found, problems = parse_freeze(text)
    assert found == {"NIFTY": 1800, "BANKNIFTY": 900}
    assert problems == [
        "line 5: expected underlying,freeze_qty, got 'FINNIFTY'",
        "line 6: MIDCPNIFTY's freeze quantity must be a whole number above 0, got 'lots'",
        "line 7: SENSEX's freeze quantity must be a whole number above 0, got '0'",
        "line 8: NIFTY is listed twice (the first is kept)",
    ]


def test_a_bad_file_never_stops_the_server_but_is_logged(tmp_path, nifty_fut, caplog):
    store = store_with(nifty_fut)
    path = tmp_path / "freeze.csv"
    path.write_text("NIFTY,1800\nBAD LINE\n")
    with caplog.at_level(logging.WARNING):
        problems = apply_freeze(store, path)
    assert problems == ["line 2: expected underlying,freeze_qty, got 'BAD LINE'"]
    assert "BAD LINE" in caplog.text
    assert store.get(FUT).freeze_qty == 1800
    assert apply_freeze(store, tmp_path / "gone.csv")[0].startswith("can't read it")
    assert store.get(FUT).freeze_qty is None  # nothing read: no limit claimed


def test_the_setting_must_name_a_file_that_can_be_read(tmp_path):
    from test_serve import env

    with pytest.raises(ConfigError, match="MOCKTRADE_FREEZE_FILE can't be read"):
        ServeSettings.from_env(env(MOCKTRADE_FREEZE_FILE=str(tmp_path / "gone.csv")))
    path = tmp_path / "freeze.csv"
    path.write_text("NIFTY,1800\n")
    assert ServeSettings.from_env(env(MOCKTRADE_FREEZE_FILE=str(path))).freeze_file == path


# --- the instruments ------------------------------------------------------------------------


def test_futures_and_options_take_their_underlyings_limit_now_and_after_a_refresh(
    nifty_fut, reliance
):
    # the share carries its name as an underlying, as its own futures do
    store = store_with(nifty_fut, replace(reliance, underlying="RELIANCE"),
                       {"NIFTY": 1800, "RELIANCE": 500})  # fmt: skip
    assert store.get(FUT).freeze_qty == 1800
    assert store.get("NSE:RELIANCE").freeze_qty is None  # shares: not a derivative
    store.add(replace(reliance, underlying="RELIANCE"))  # nor when added after the file
    assert store.get("NSE:RELIANCE").freeze_qty is None
    nov = replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24))
    store.add(nov)  # the daily refresh adds contracts later
    assert store.get(NOV).freeze_qty == 1800
    store.add(replace(nifty_fut, tick_paise=5))  # and replaces today's
    assert store.get(FUT).freeze_qty == 1800 and store.get(FUT).tick_paise == 5
    assert store.set_freeze({}) == []  # a limit taken away is cleared
    assert store.get(FUT).freeze_qty is None and store.get(NOV).freeze_qty is None


def test_a_limit_below_one_lot_is_not_applied_and_said(nifty_fut):
    store = store_with(nifty_fut)
    assert store.set_freeze({"NIFTY": 50}) == [
        "NIFTY: freeze quantity 50 is below one lot (75): not applied"
    ]
    assert store.get(FUT).freeze_qty is None
    with pytest.raises(ValueError, match="freeze quantity must be at least one lot"):
        replace(nifty_fut, freeze_qty=50)


def test_one_order_may_carry_the_most_whole_lots_under_the_limit(nifty_fut):
    assert replace(nifty_fut, freeze_qty=1800).max_order_qty == 1800
    assert replace(nifty_fut, freeze_qty=1000).max_order_qty == 975
    assert nifty_fut.max_order_qty is None
    assert instrument_from_record(instrument_record(replace(nifty_fut, freeze_qty=1800))) == (
        replace(nifty_fut, freeze_qty=1800)
    )  # kept with a recorded day, so a replay knows it
    old = {k: v for k, v in instrument_record(nifty_fut).items() if k != "freeze_qty"}
    assert instrument_from_record(old) == nifty_fut  # days recorded before #424


# --- every order path -----------------------------------------------------------------------


def order(qty, *, cid="MT1", instrument_id=FUT):
    return OrderRequest(cid, "acc-1", "ayush", instrument_id, Side.BUY, qty, OrderType.LIMIT,
                        Product.NRML, price_paise=2501000)  # fmt: skip


def test_an_order_above_the_limit_is_refused_in_words(nifty_fut):
    frozen = replace(nifty_fut, freeze_qty=1800)
    order(1800).validate(frozen)
    with pytest.raises(ValueError) as refused:
        order(1875).validate(frozen)
    assert str(refused.value) == (
        "1875 is more than NIFTY26OCTFUT's freeze quantity 1800, the exchange's most per "
        "order: split it"
    )
    order(1875).validate(nifty_fut)  # no limit known: not refused


async def test_the_paper_broker_refuses_what_the_exchange_would(nifty_fut):
    store = store_with(nifty_fut, freeze={"NIFTY": 1800})
    paper = PaperBroker(MarketData(), store, latency_s=0.0)
    with pytest.raises(BrokerError, match="freeze quantity 1800"):
        await paper.place(order(1875))
    placed = await paper.place(order(1800, cid="MT2"))
    with pytest.raises(BrokerError, match="freeze quantity 1800"):
        await paper.modify(placed, qty=1875)
    await paper.aclose()


def test_the_risk_gate_and_the_order_spec_know_the_limit(nifty_fut):
    store = store_with(nifty_fut, freeze={"NIFTY": 1000})
    gate = RiskGate(RiskLimits(max_order_lots=50, max_order_value_paise=10**15),
                    instruments=store.get, prices=lambda _i: None, position=lambda _i: 0,
                    working=list, day_pnl_paise=lambda: 0, switches=())  # fmt: skip
    assert gate.max_order_qty(FUT, 2501000) == 975  # the freeze, not 50 lots
    gate.limits = RiskLimits(max_order_lots=4, max_order_value_paise=10**15)
    assert gate.max_order_qty(FUT, 2501000) == 300  # the account's own limit is smaller
    spec = standard_spec(store.get(FUT), auto_protection_pct=2, max_modifications=25)
    assert spec_to_dict(spec)["freeze_qty"] == 975
    assert spec_to_dict(standard_spec(nifty_fut, auto_protection_pct=2,
                                      max_modifications=25))["freeze_qty"] is None  # fmt: skip


async def test_a_forced_close_goes_in_pieces_that_fit_the_limit(nifty_fut):
    frozen = replace(nifty_fut, freeze_qty=1000)
    sent = []

    class Orders:
        account_id = "acc-1"

        async def submit(self, request):
            sent.append(request.qty)
            return request

    await close_position(Orders(), frozen, Product.NRML, 2250, user_id="ayush", max_lots=50,
                         basket_id="B1")  # fmt: skip
    assert sent == [975, 975, 300]


async def test_on_the_paper_broker_an_ase_hedge_is_split_at_the_freeze(nifty_fut):
    """30 calendars with generous account limits: each order fits 24 lots (1,800), so the
    quote goes 24 then 6 and each hedge the same; every order fills, none is refused."""
    store = store_with(nifty_fut)
    store.add(replace(nifty_fut, symbol="NIFTY26NOVFUT", expiry=date(2026, 11, 24)))
    store.set_freeze({"NIFTY": 1800})
    md = MarketData()

    def prices():
        md.on_tick(Tick(FUT, 2500000, bids=(DepthLevel(2500000, 5000, 9),),
                        asks=(DepthLevel(2501000, 5000, 9),)))  # fmt: skip
        md.on_tick(Tick(NOV, 2514000, bids=(DepthLevel(2514000, 5000, 9),),
                        asks=(DepthLevel(2514010, 5000, 9),)))  # fmt: skip

    prices()
    book = SpreadBook(None, store.get)
    legs = [SpreadLeg(FUT, 1, 1, quote=True, payup_ticks=2), SpreadLeg(NOV, -1, -1, payup_ticks=2)]
    cal = book.create(User("ayush", "Ayush"), "Oct/Nov", legs)
    platform = Platform(store, md, KillSwitch("firm"))
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"), paper, ids=ClientIds("MT"),
        limits=RiskLimits(max_order_lots=50, max_order_value_paise=10**15,
                          max_position_lots=100),
        algo_types=(*BUILTIN, algo_type(book.get)),
    )  # fmt: skip
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        algo = runtime.algos.start("ASE", user_id="ayush", instrument_id=FUT, side=Side.BUY,
                                   qty=30 * 75, params={"spread": cal.spread_id,
                                                        "level_paise": -13000})  # fmt: skip
        async with asyncio.timeout(5):
            while runtime.algos.get(algo.algo_id).state is not AlgoState.DONE:
                await asyncio.sleep(0.02)
                prices()
        sizes = sorted((o.request.instrument_id, o.request.qty) for o in runtime.orders.orders())
        assert sizes == [(NOV, 450), (NOV, 1800), (FUT, 450), (FUT, 1800)]
        assert [runtime.positions.net_qty(i) for i in (FUT, NOV)] == [2250, -2250]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()


def test_demo_and_simulated_servers_carry_sample_limits():
    from mocktrade.marketdata.sim_universe import FREEZE, INSTRUMENTS

    store = InstrumentStore()
    for instrument, _ in INSTRUMENTS:
        store.add(instrument)
    assert store.set_freeze(FREEZE) == []
    nifty = [i for i in (store.get(x.id) for x, _ in INSTRUMENTS) if i.underlying == "NIFTY"]
    assert nifty and all(i.freeze_qty == 1800 for i in nifty)


def test_an_instrument_with_a_freeze_says_it_over_the_api():
    from mocktrade.server.app import _instrument

    nifty = Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, 10, 75,
                       date(2026, 10, 27), None, "NIFTY", freeze_qty=1800)  # fmt: skip
    assert _instrument(nifty)["freeze_qty"] == 1800
