"""Whose is what (#270): backtests are their owner's (an admin sees all); someone else's is
"no backtest", exactly like one that doesn't exist; the shared candle dataset is imported by
admins only; alerts carry the account they belong to. Market alert ownership and per-account
streams are tested where they live (test_market_alerts, test_stream)."""

import asyncio

from test_backtest import DAY, MODEL, day_ticks
from test_server import ORIGIN
from test_server import World as ServerWorld

from laddrix.ops.alerts import Alerts, alert_to_dict
from laddrix.research.backtest import Backtests, CandleStore
from laddrix.server.app import ServerSettings, create_app


async def test_backtests_are_their_owners_and_an_admin_sees_all(tmp_path, nifty_fut):
    w = ServerWorld(tmp_path, nifty_fut)
    fut = nifty_fut.id
    runs = Backtests(tmp_path / "bt", w.platform.instruments, MODEL,
                     ticks=lambda d: iter(day_ticks(fut)) if d == DAY else None)  # fmt: skip
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), backtests=runs,
                       candles=CandleStore(tmp_path / "candles"))  # fmt: skip
    riya, risk, ayush = [await w.logged_in(u) for u in ("riya", "risk", "ayush")]
    body = {"days": [DAY.isoformat()], "algo": "SLICER", "instrument_id": fut, "side": "BUY",
            "qty": 150, "params": {"slices": 2, "interval_s": 60, "mode": "cross"}}  # fmt: skip
    mine = (await riya.post("/api/research/backtests", json=body)).json()
    await runs.settle()
    assert mine["owner"] == "riya"
    run = mine["run_id"]
    assert [r["run_id"] for r in (await riya.get("/api/research/backtests")).json()] == [run]
    assert (await risk.get("/api/research/backtests")).json() == []  # not risk's
    for path in (f"/api/research/backtests/{run}", f"/api/research/backtests/{run}/days/{DAY}"):
        other = await risk.get(path)
        assert other.status_code == 404
        assert other.json()["detail"] == f"no backtest {run}"  # the same as no run at all
    assert (await ayush.get(f"/api/research/backtests/{run}")).status_code == 200  # admin
    for c in (riya, risk, ayush):
        await c.aclose()
    w.log.close()
    await asyncio.sleep(0)


async def test_the_shared_candle_dataset_is_imported_by_admins_only(tmp_path, nifty_fut):
    w = ServerWorld(tmp_path, nifty_fut)
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN),
                       candles=CandleStore(tmp_path / "candles"))  # fmt: skip
    riya, ayush = [await w.logged_in(u) for u in ("riya", "ayush")]
    rows = [{"time": "2026-10-16T04:00:00+00:00", "open": 1, "high": 2, "low": 1, "close": 2}]
    body = {"instrument_id": nifty_fut.id, "day": "2026-10-16", "rows": rows}
    refused = await riya.post("/api/research/candles", json=body)
    assert refused.status_code == 403 and "only an admin can import candles" in refused.text
    assert (await ayush.post("/api/research/candles", json=body)).status_code == 201
    for c in (riya, ayush):
        await c.aclose()
    w.log.close()
    await asyncio.sleep(0)


def test_backtests_kept_from_before_have_no_owner_and_only_admins_see_them(tmp_path):
    from laddrix.research.backtest import BacktestSpec, Run

    old = Run("BT1", BacktestSpec(("2026-10-16",), "SLICER", "X", "BUY", 75, {}), False)
    assert old.owner == "" and not old.may_see("riya", admin=False) and old.may_see("a", True)


def test_an_alert_says_whose_it_is_and_its_end_goes_to_the_same_people():
    alerts, told = Alerts(), []
    alerts.subscribe(told.append)
    alerts.raise_("mtm:acc-riya:portfolio", "ERROR", "…", account_id="acc-riya")
    alerts.clear("mtm:acc-riya:portfolio", "closed")
    assert [a.account_id for a in told] == ["acc-riya", "acc-riya"]
    assert alert_to_dict(told[0])["account_id"] == "acc-riya"


# --- every account's alert is tagged with its account (else it would reach everyone) --------


def test_the_kite_login_reminder_belongs_to_its_account():
    from test_ops import WED, Clock, checks, ist

    c, alerts = checks(Clock(ist(WED, 8, 30)), status=lambda a: "login_needed")
    c.kite_login()
    tags = {a.key: a.account_id for a in alerts.active()}
    assert tags == {"broker_login:acc-ayush": "acc-ayush", "broker_login:acc-paper": "acc-paper"}


def test_a_lost_stop_band_belongs_to_its_account():
    from test_protection_guard import FUT, algo, setup

    from laddrix.algos.engine import AlgoState

    p, alerts, _ = setup()
    p.accounts["acc"].positions.net[FUT] = 75
    p.accounts["acc"].algos.emit("STOPPED", algo(state=AlgoState.STOPPED))
    (band,) = alerts.active()
    assert band.account_id == "acc"


async def test_mtm_and_square_off_bands_belong_to_their_account(nifty_fut, tmp_path):
    from test_mtm import Clock, move
    from test_portfolio import FUT, Paper

    from laddrix.domain.orders import Product
    from laddrix.ops.guards import DayGuards
    from laddrix.risk.mtm import PositionRule

    clock = Clock(15, 5)
    p = Paper(nifty_fut, tmp_path, clock=clock)
    alerts = Alerts()
    guards = DayGuards(p.platform, alerts)
    await p.buy(FUT, 75, 2501010, Product.MIS)
    p.rt.mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_00), "ayush")
    await move(p, 2499000)
    await guards.check()
    p.rt.mtm.problem = "rules: test"
    guards.check_mtm()
    tags = {a.key: a.account_id for a in alerts.active()}
    assert tags == {f"mtm:acc:{FUT}|MIS": "acc", "squareoff:acc": "acc", "mtm-data:acc": "acc"}
    await p.stop()


def test_a_failed_restart_band_belongs_to_its_account():
    from laddrix.tools.serve import _recovery_alert

    alerts = Alerts()
    _recovery_alert(alerts, "acc-riya", "failed", "the broker can't be reached")
    (band,) = alerts.active()
    assert band.account_id == "acc-riya"
