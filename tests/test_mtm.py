"""MTM limits and the square-off assistant on a simulated day (K10.5, #217).

A paper account trades NIFTY futures (lot 75, at 25,010 to start); the market moves by ticks
and the clock is set by hand, so the day runs in a second.
"""

import asyncio
import json
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from test_portfolio import FUT, REL, Paper, book

from mocktrade.domain.orders import Product, Side
from mocktrade.ops.alerts import Alerts
from mocktrade.ops.guards import DayGuards
from mocktrade.risk.mtm import MtmWatch, PortfolioRule, PositionRule, rule_problems

IST = ZoneInfo("Asia/Kolkata")


class Clock:
    def __init__(self, hh=10, mm=0):
        self.set(hh, mm)

    def set(self, hh, mm=0):
        self.now = datetime(2026, 10, 16, hh, mm, tzinfo=IST).astimezone(UTC)

    def __call__(self):
        return self.now


async def move(p, ltp, instrument=FUT):
    """The market moves; positions are marked (the platform's mark loop)."""
    p.md.on_tick(book(instrument, ltp))
    p.platform.mark(p.md.latest(instrument))
    await asyncio.sleep(0.05)


async def flat(p, instrument=FUT, product=None):
    async with asyncio.timeout(5):
        while any(
            v.qty
            for v in p.rt.positions.positions()
            if v.instrument_id == instrument and product in (None, v.product)
        ):
            await asyncio.sleep(0.01)


@pytest.fixture
async def day(nifty_fut, tmp_path):
    clock = Clock()
    p = Paper(nifty_fut, tmp_path, clock=clock)
    p.clock = clock
    yield p
    await p.stop()


def notes(p, kind):
    return [e.detail for e in p.log.events(account_id="acc") if e.kind == kind]


async def test_a_position_limit_alerts_once_and_closes_only_when_asked(day):
    p = day
    seen = []
    p.rt.mtm.subscribe(seen.append)
    await p.buy(FUT, 75, 2501010, Product.MIS)
    p.rt.mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_500_00), "ayush")
    await move(p, 2499000)  # 75 × −20.10 = −₹1,507.50: just past the ₹1,500 limit
    assert len(seen) == 1
    await move(p, 2498000)  # further down: no second alert today
    assert len(seen) == 1 and seen[0].key == f"{FUT}|MIS" and not seen[0].closing
    assert "reached the loss limit ₹1,500.00; alert only (auto-close is off)" in seen[0].text
    assert p.rt.positions.net_qty(FUT) == 75  # alert only: nothing sent
    p.rt.mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=3_000_00), "ayush")
    await move(p, 2496000)  # a new limit is watched afresh: −₹3,757.50
    assert len(seen) == 2
    await p.log.flush()
    assert notes(p, "MTM_RULE")[0] == f"{FUT}|MIS: loss ₹1,500.00, alert only (by ayush)"
    assert len(notes(p, "MTM_BREACHED")) == 2


async def test_a_percent_limit_with_auto_close_exits_through_the_normal_path(day):
    p = day
    await p.buy(FUT, 150, 2501010, Product.NRML)  # worth ₹37,51,515
    p.rt.mtm.set_position_rule(
        PositionRule(FUT, "NRML", max_loss_pct=0.5, auto_close=True), "ayush"
    )
    await move(p, 2490000)  # 150 × −110.10 = −₹16,515: 0.44%, not yet
    assert p.rt.positions.net_qty(FUT) == 150
    await move(p, 2488000)  # −₹19,515: 0.52%
    await p.rt.mtm.settle()
    await flat(p)
    closing = [o for o in p.rt.orders.orders() if (o.request.basket_id or "").startswith("MTM")]
    assert [(o.request.side, o.request.qty, o.request.product) for o in closing] == [
        (Side.SELL, 150, Product.NRML)]  # fmt: skip
    await p.log.flush()
    assert "0.52% loss (limit 0.5%)" in notes(p, "MTM_BREACHED")[0]
    assert notes(p, "MTM_CLOSE")[0].startswith(f"closing +150 {FUT} NRML: ")


async def test_the_portfolio_stop_closes_everything_and_a_target_fires_too(day):
    p = day
    await p.buy(FUT, 75, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2501010, Product.NRML)
    await p.buy(REL, 10, 250010, Product.MIS)
    p.rt.mtm.set_portfolio_rule(PortfolioRule(max_loss_paise=2_000_00, auto_close=True), "ayush")
    await move(p, 2499000)  # 150 × −20.10 = −₹3,015 (+ RELIANCE −₹0.10)
    await p.rt.mtm.settle()
    await flat(p)
    await flat(p, REL)
    assert p.rt.mtm.to_dict()["portfolio"]["fired_today"] is True
    with pytest.raises(ValueError, match="give a loss stop or a profit target"):
        p.rt.mtm.set_portfolio_rule(PortfolioRule(), "ayush")
    p.rt.mtm.set_portfolio_rule(PortfolioRule(target_paise=1), "ayush")  # watched afresh
    events = p.rt.mtm.check()
    assert events == [] or "target" in events[0].text  # the day's P&L is below zero


def test_rule_problems_are_named():
    assert rule_problems(PositionRule(FUT, "MIS")) == ["give a loss limit in rupees or percent"]
    assert rule_problems(PositionRule(FUT, "MIS", max_loss_paise=0)) == [
        "the loss limit must be above 0"]  # fmt: skip
    assert rule_problems(PositionRule(FUT, "MIS", max_loss_pct=150)) == [
        "the percent must be between 0 and 100"]  # fmt: skip
    assert rule_problems(PortfolioRule(target_paise=-1)) == ["stops and targets must be above 0"]
    assert rule_problems(PortfolioRule(max_loss_paise=1)) == []


async def test_rules_survive_a_restart_and_a_broken_file_loads_none(day, tmp_path):
    p = day
    path = tmp_path / "rules" / "mtm-acc.json"
    watch = MtmWatch("acc", p.rt.positions, p.rt.orders, p.store.get, max_lots=10, path=path)
    watch.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=100, auto_close=True), "a")
    watch.set_portfolio_rule(PortfolioRule(max_loss_paise=500), "a")
    again = MtmWatch("acc", p.rt.positions, p.rt.orders, p.store.get, max_lots=10, path=path)
    assert again.to_dict() == watch.to_dict()
    path.write_text("{not json")
    assert MtmWatch("acc", p.rt.positions, p.rt.orders, p.store.get, max_lots=10,
                    path=path).to_dict() == {"positions": [], "portfolio": None}  # fmt: skip
    json.loads(json.dumps(again.to_dict()))  # what the API sends is plain JSON


# --- the square-off assistant -------------------------------------------------------------------


async def test_square_off_window_warning_and_one_click_closes_only_mis(day):
    p = day
    await p.buy(FUT, 75, 2501010, Product.MIS)
    await p.buy(FUT, 75, 2501010, Product.NRML)  # carried overnight: not squared off
    so = p.rt.squareoff
    p.clock.set(14, 50)
    assert so.warning() is None and so.status()["window_open"] is False
    p.clock.set(15, 5)
    status = so.status()
    assert status["window_open"] and status["cutoffs"]["NFO"] == "15:25"
    assert [(r["instrument_id"], r["qty"], r["minutes_left"]) for r in status["positions"]] == [
        (FUT, 75, 20)]  # fmt: skip
    assert so.warning() == ("acc: 1 intraday (MIS) position open (NIFTY26OCTFUT +75); "
                            "the broker squares off in 20 min. Square off now?")  # fmt: skip
    done = await so.close_all("ayush")
    assert [(d["instrument_id"], d["qty"], d["refused"]) for d in done] == [(FUT, 75, [])]
    await flat(p, product=Product.MIS)
    assert p.rt.positions.net_qty(FUT) == 75  # the NRML position stays
    assert so.warning() is None
    await p.log.flush()
    assert notes(p, "SQUARE_OFF")[0].startswith(f"ayush squared off +75 {FUT} MIS: ")


async def test_a_big_position_closes_in_pieces_none_refused_as_a_double_click(day):
    from mocktrade.risk.squareoff import SquareOff

    p = day
    await p.buy(FUT, 1500, 2501010, Product.MIS)  # 20 lots
    so = SquareOff("acc", p.rt.positions, p.rt.orders, p.store.get, max_lots=10)
    done = await so.close_all("ayush")
    assert len(done[0]["orders"]) == 2 and done[0]["refused"] == []  # 2 × 10 lots, identical
    await flat(p, product=Product.MIS)


async def test_day_guards_raise_and_clear_band_alerts(day):
    p = day
    alerts = Alerts()
    guards = DayGuards(p.platform, alerts)
    await p.buy(FUT, 75, 2501010, Product.MIS)
    p.rt.mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00), "ayush")
    p.clock.set(15, 10)
    await move(p, 2499000)
    await guards.check()
    keys = {a.key: a.level for a in alerts.active()}
    assert keys == {f"mtm:acc:{FUT}|MIS": "ERROR", "squareoff:acc": "WARN"}
    await p.rt.squareoff.close_all("ayush")
    await flat(p)
    await guards.check()
    assert alerts.active() == []  # closed: both cleared


async def test_the_mtm_and_square_off_api(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)
    c = await w.logged_in()
    base = "/api/accounts/acc-ayush"
    rule = {"instrument_id": FUT, "product": "MIS", "max_loss_paise": 150000, "auto_close": True}
    r = await c.put(f"{base}/mtm/positions", json=rule)
    assert r.status_code == 200, r.text
    assert r.json()["positions"][0]["key"] == f"{FUT}|MIS"
    bad = await c.put(f"{base}/mtm/positions", json={"instrument_id": FUT, "product": "MIS"})
    assert bad.status_code == 422 and "give a loss limit" in bad.json()["detail"]
    r = await c.put(f"{base}/mtm/portfolio", json={"max_loss_paise": 500000})
    assert r.json()["portfolio"]["max_loss_paise"] == 500000
    r = await c.delete(f"{base}/mtm/positions?instrument_id={FUT}&product=MIS")
    assert r.json()["positions"] == []
    assert (await c.delete(f"{base}/mtm/portfolio")).json()["portfolio"] is None
    status = (await c.get(f"{base}/square-off")).json()
    assert status["cutoffs"] == {"BFO": "15:25", "BSE": "15:20", "NFO": "15:25", "NSE": "15:20"}
    assert (await c.post(f"{base}/square-off", json={"confirm": False})).status_code == 422
    assert (await c.post(f"{base}/square-off", json={"confirm": True})).json() == {"closed": []}
    await c.aclose()
    w.log.close()


def test_cutoffs_are_data():
    from mocktrade.risk.squareoff import CUTOFFS, WARN_FROM

    assert CUTOFFS["NFO"] == time(15, 25) and WARN_FROM == time(15, 0)
