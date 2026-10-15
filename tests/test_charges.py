"""Charges on Indian trades, worked by hand per segment (K10.2, #214).

Each expected figure is written out from the rates in charges/schedules.py (checked 15 Oct
2026), in rupees, then turned to paise; each line is rounded to the paisa once, at the end.
"""

import asyncio
import json
from datetime import date
from fractions import Fraction

import pytest

from mocktrade.charges.model import ExecutedOrder, ScheduleCharges, Segment, segment_of
from mocktrade.charges.schedules import (
    KITE_STANDARD,
    load_schedule,
    schedule_from_dict,
    schedule_to_dict,
)
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import Product, Side

CE = Instrument(Exchange.NFO, "NIFTY26OCT25000CE", InstrumentKind.CALL, 5, 75,
                date(2026, 10, 27), 2500000, "NIFTY")  # fmt: skip
FUT = Instrument(Exchange.NFO, "NIFTY26OCTFUT", InstrumentKind.FUTURE, 10, 75,
                 date(2026, 10, 27), None, "NIFTY")  # fmt: skip
REL = Instrument(Exchange.NSE, "RELIANCE", InstrumentKind.EQUITY, 10, 1)
REL_BSE = Instrument(Exchange.BSE, "RELIANCE", InstrumentKind.EQUITY, 5, 1)
MODEL = ScheduleCharges(KITE_STANDARD)


def order(instrument, side, qty, rupees, product=Product.NRML, oid=None):
    value = Fraction(rupees) * 100 * qty
    return ExecutedOrder(oid or f"{side.value}{qty}{rupees}", instrument, product, side, qty, value)


def lines(bill):
    return {line.label: line.paise for line in bill.lines}


def test_segments():
    assert segment_of(REL, Product.CNC) is Segment.DELIVERY
    assert segment_of(REL, Product.MIS) is Segment.INTRADAY
    assert segment_of(FUT, Product.NRML) is Segment.FUTURES
    assert segment_of(CE, Product.MIS) is Segment.OPTIONS
    index = Instrument(Exchange.NSE, "NIFTY 50", InstrumentKind.INDEX, 5, 1)
    assert segment_of(index, Product.MIS) is None


def test_options_on_the_premium_flat_brokerage_per_order():
    # buy 75 @ ₹100 (₹7,500), sell 75 @ ₹110 (₹8,250): turnover ₹15,750
    bill = MODEL.bill([order(CE, Side.BUY, 75, 100), order(CE, Side.SELL, 75, 110)])
    assert lines(bill) == {
        "Brokerage": 4000,  # ₹20 + ₹20
        "STT": 1238,  # 0.15% of the sell premium ₹8,250 = ₹12.375
        "Exchange charges": 552,  # 0.03503% of ₹15,750 = ₹5.517225
        "SEBI fee": 2,  # 0.0001% of ₹15,750 = ₹0.01575
        "IPFT": 8,  # 0.0005% of ₹15,750 = ₹0.07875
        "Stamp duty": 23,  # 0.003% of the buy ₹7,500 = ₹0.225
        "GST": 821,  # 18% of (40 + 5.517225 + 0.01575 + 0.07875) = ₹8.2101105
        "DP charges": 0,
    }
    assert bill.total_paise == 6644 and bill.turnover_paise == 15750_00
    assert bill.by_position == {f"{CE.id}|NRML": 6644}


def test_futures_brokerage_capped_at_20_rupees_an_order():
    # buy 75 @ ₹25,000 (₹18,75,000), sell 75 @ ₹25,100 (₹18,82,500): turnover ₹37,57,500
    bill = MODEL.bill([order(FUT, Side.BUY, 75, 25000), order(FUT, Side.SELL, 75, 25100)])
    assert lines(bill) == {
        "Brokerage": 4000,  # 0.03% would be ₹562.50 an order: capped at ₹20
        "STT": 94125,  # 0.05% of the sell ₹18,82,500 = ₹941.25 (Budget 2026)
        "Exchange charges": 6500,  # 0.00173% of ₹37,57,500 = ₹65.00475
        "SEBI fee": 376,  # ₹3.7575
        "IPFT": 376,  # ₹3.7575
        "Stamp duty": 3750,  # 0.002% of the buy = ₹37.50
        "GST": 2025,  # 18% of (40 + 65.00475 + 3.7575 + 3.7575) = ₹20.253555
        "DP charges": 0,
    }
    assert bill.total_paise == 111152


def test_intraday_brokerage_below_the_cap_and_stt_on_the_sell_only():
    # MIS: buy 10 @ ₹2,500 (₹25,000), sell 10 @ ₹2,510 (₹25,100): turnover ₹50,100
    bill = MODEL.bill([order(REL, Side.BUY, 10, 2500, Product.MIS),
                       order(REL, Side.SELL, 10, 2510, Product.MIS)])  # fmt: skip
    assert lines(bill) == {
        "Brokerage": 1503,  # 0.03%: ₹7.50 + ₹7.53 (under the ₹20 cap)
        "STT": 628,  # 0.025% of ₹25,100 = ₹6.275
        "Exchange charges": 149,  # 0.00297% of ₹50,100 = ₹1.48797
        "SEBI fee": 5,
        "IPFT": 5,
        "Stamp duty": 75,  # 0.003% of ₹25,000
        "GST": 299,  # 18% of (15.03 + 1.48797 + 0.0501 + 0.0501) = ₹2.9912706
        "DP charges": 0,
    }


def test_each_position_its_own_charges_and_they_add_up():
    bill = MODEL.bill([order(REL, Side.BUY, 10, 2500, Product.CNC),
                       order(REL, Side.BUY, 10, 2500, Product.MIS),
                       order(CE, Side.BUY, 75, 101)])  # fmt: skip
    assert set(bill.by_position) == {"NSE:RELIANCE|CNC", "NSE:RELIANCE|MIS", f"{CE.id}|NRML"}
    assert sum(bill.by_position.values()) == bill.total_paise


def test_delivery_stt_both_sides_and_dp_once_a_stock_a_day():
    # CNC: buy 10 @ ₹2,500; sold in two orders of 5 @ ₹2,510
    bill = MODEL.bill([
        order(REL, Side.BUY, 10, 2500, Product.CNC),
        order(REL, Side.SELL, 5, 2510, Product.CNC, oid="S1"),
        order(REL, Side.SELL, 5, 2510, Product.CNC, oid="S2"),
    ])  # fmt: skip
    assert lines(bill) == {
        "Brokerage": 0,
        "STT": 5010,  # 0.1% of ₹50,100
        "Exchange charges": 149,
        "SEBI fee": 5,
        "IPFT": 5,
        "Stamp duty": 375,  # 0.015% of ₹25,000
        "GST": 29,  # 18% of (1.48797 + 0.0501 + 0.0501) = ₹0.2858706
        "DP charges": 1534,  # ₹15.34 once, not once per sell order
    }


def test_the_exchange_decides_the_exchange_fee():
    nse = MODEL.bill([order(REL, Side.BUY, 100, 1000, Product.CNC)])
    bse = MODEL.bill([order(REL_BSE, Side.BUY, 100, 1000, Product.CNC)])
    assert lines(nse)["Exchange charges"] == 297  # 0.00297% of ₹1,00,000
    assert lines(bse)["Exchange charges"] == 375  # 0.00375%
    assert lines(bse)["IPFT"] == 0  # NSE's fund


def test_nothing_executed_costs_nothing():
    assert MODEL.bill([order(CE, Side.BUY, 0, 100)]).total_paise == 0  # no flat ₹20 for nothing
    bill = MODEL.bill([])
    assert bill.total_paise == 0 and bill.by_position == {}
    assert [line.label for line in bill.lines][0] == "Brokerage"


def test_a_schedule_round_trips_through_json_and_bad_ones_are_named(tmp_path):
    data = schedule_to_dict(KITE_STANDARD)
    assert data["segments"]["futures"]["stt_sell_pct"] == "0.05"
    assert data["segments"]["options"]["exchange_pct"]["NSE"] == "0.03503"
    path = tmp_path / "charges.json"
    path.write_text(json.dumps(data))
    assert load_schedule(path) == KITE_STANDARD
    budget = json.loads(json.dumps(data))
    budget["segments"]["futures"]["stt_sell_pct"] = "0.1"
    raised = ScheduleCharges(schedule_from_dict(budget))
    assert lines(raised.bill([order(FUT, Side.SELL, 75, 25100)]))["STT"] == 188250
    del budget["segments"]["options"]
    with pytest.raises(ValueError, match="no rates for options"):
        schedule_from_dict(budget)
    with pytest.raises(ValueError, match="can't read"):
        schedule_from_dict({"name": "x", "checked_on": "15-10-2026", "segments": {}})


# --- the service, the API ---------------------------------------------------------------------


async def test_partial_fills_of_one_order_pay_brokerage_once(nifty_fut):
    from test_portfolio import Paper  # a platform with a paper broker

    p = Paper(nifty_fut)
    seen = []
    p.rt.charges.subscribe(lambda account_id, bill: seen.append(bill))
    await p.buy("NFO:NIFTY26OCTFUT", 150, 2501010, Product.NRML)  # 2 lots in one order
    await asyncio.sleep(0)
    bill = p.rt.charges.bill()
    assert lines(bill)["Brokerage"] == 2000  # one executed order: ₹20, however many fills
    assert seen and seen[-1] == bill
    assert bill.by_position == {"NFO:NIFTY26OCTFUT|NRML": bill.total_paise}
    est = p.rt.charges.estimate("NFO:NIFTY26OCTFUT", Side.SELL, 75, 2510000, Product.NRML)
    assert lines(est)["STT"] == 94125  # 0.05% of ₹18,82,500
    await p.stop()


async def test_the_charges_api(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)
    c = await w.logged_in()
    schedule = (await c.get("/api/charges/schedule")).json()
    assert schedule["name"] == "Kite standard plan" and schedule["checked_on"] == "2026-10-15"
    r = await c.post("/api/accounts/acc-ayush/charges/estimate", json={
        "instrument_id": "NFO:NIFTY26OCTFUT", "side": "SELL", "qty": 75,
        "price_paise": 2510000, "product": "NRML"})  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert {x["label"]: x["paise"] for x in body["lines"]}["STT"] == 94125
    assert body["schedule"]["name"] == "Kite standard plan"
    r = await c.post("/api/accounts/acc-ayush/charges/estimate", json={
        "instrument_id": "NFO:NOPE", "side": "SELL", "qty": 75, "price_paise": 1,
        "product": "NRML"})  # fmt: skip
    assert r.status_code == 422 and "unknown instrument" in r.json()["detail"]
    today = (await c.get("/api/accounts/acc-ayush/charges")).json()
    assert today["total_paise"] == 0 and today["account_id"] == "acc-ayush"
    await c.aclose()
    w.log.close()


def test_only_todays_orders_and_estimates_need_a_quantity_and_a_price():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace as NS

    from mocktrade.charges.service import ChargesError, ChargesService

    now = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 11:30 in India

    def view(cid, when, side=Side.BUY):
        request = NS(instrument_id=FUT.id, product=Product.NRML, side=side)
        return NS(client_order_id=cid, created_at=when, filled_qty=75, avg_price_paise=2500000,
                  request=request)  # fmt: skip

    orders = NS(orders=lambda: [view("Y", now - timedelta(days=1), Side.SELL), view("T", now)])
    service = ChargesService("acc", MODEL, orders, {FUT.id: FUT}.get, clock=lambda: now)
    today = lines(service.bill())
    assert today["Brokerage"] == 2000 and today["STT"] == 0  # yesterday's sell isn't today's
    assert today["Stamp duty"] == 3750  # today's buy: 0.002% of ₹18,75,000
    for qty, price in ((0, 2500000), (75, 0)):
        with pytest.raises(ChargesError, match="needs a quantity and a price"):
            service.estimate(FUT.id, Side.BUY, qty, price, Product.NRML)
    with pytest.raises(ChargesError, match="unknown instrument"):
        service.estimate("NFO:NOPE", Side.BUY, 75, 1, Product.NRML)
