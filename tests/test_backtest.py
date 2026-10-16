"""Backtests (K11.3, #225): the same algo engine, paper broker and risk checks as live, over
recorded days; checked against a hand-worked day, and the same every time.

The hand-worked day (NIFTY futures, lot 75; each tick: bid ltp − 0.10, ask ltp + 0.10):

    09:15:00  25,000.00   SLICER BUY 150, 2 slices 60 s apart, crossing: 1st at the ask 25,000.10
    09:15:30  25,001.00
    09:16:00  25,002.00   slice 2 at the ask 25,002.10
    09:16:30  25,003.00
    09:20:00  25,005.00   the last price
    → long 150; unrealised 75 × 4.90 + 75 × 2.90 = ₹585.00; charges: the two buy orders
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mocktrade.charges.model import ExecutedOrder, ScheduleCharges
from mocktrade.charges.schedules import KITE_STANDARD
from mocktrade.domain.orders import Product, Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.refdata.store import InstrumentStore
from mocktrade.research.backtest import (
    EPOCH,
    BacktestError,
    Backtests,
    BacktestSpec,
    CandleStore,
    candles_to_ticks,
    run_day,
    summarise,
)

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 16)
T0 = datetime(2026, 10, 16, 9, 15, tzinfo=IST)
MODEL = ScheduleCharges(KITE_STANDARD)
PLAN = [(0, 2500000), (30, 2500100), (60, 2500200), (90, 2500300), (300, 2500500)]


def book(instrument_id, ltp):
    return Tick(instrument_id, ltp, bids=(DepthLevel(ltp - 10, 150, 2),),
                asks=(DepthLevel(ltp + 10, 150, 2),))  # fmt: skip


def us(at):
    return (at - EPOCH) // timedelta(microseconds=1)


def day_ticks(fut, plan=PLAN, day=T0):
    return [(us(day + timedelta(seconds=s)), book(fut, p)) for s, p in plan]


def spec(fut, **extra):
    base = dict(days=(DAY.isoformat(),), algo="SLICER", instrument_id=fut, side="BUY", qty=150,
                params={"slices": 2, "interval_s": 60, "mode": "cross"})  # fmt: skip
    return BacktestSpec(**{**base, **extra})


@pytest.fixture
def store(nifty_fut):
    s = InstrumentStore()
    s.add(nifty_fut)
    return s


async def test_the_hand_worked_day(nifty_fut, store, tmp_path):
    fut = nifty_fut.id
    r = await run_day(spec(fut), DAY, iter(day_ticks(fut)), store, MODEL, tmp_path)
    assert [(t["ts"], t["qty"], t["price_paise"]) for t in r["trades"]] == [
        (T0.astimezone(UTC).isoformat(), 75, 2500010),  # at the time of the tick that caused it
        ((T0 + timedelta(seconds=60)).astimezone(UTC).isoformat(), 75, 2500210),
    ]  # fmt: skip
    t = r["totals"]
    assert (t["realised_paise"], t["unrealised_paise"]) == (0, 75 * 490 + 75 * 290)  # ₹585.00
    orders = [ExecutedOrder("1", nifty_fut, Product.NRML, Side.BUY, 75, 75 * 2500010),
              ExecutedOrder("2", nifty_fut, Product.NRML, Side.BUY, 75, 75 * 2500210)]  # fmt: skip
    assert t["charges_paise"] == MODEL.bill(orders).total_paise
    assert t["net_paise"] == 58500 - t["charges_paise"]
    assert r["positions"][0]["closing_qty"] == 150 and r["final"] is True
    assert (
        r["equity_curve"][0] == (T0.astimezone(UTC).isoformat(), 0)
        and r["equity_curve"][-1][1] == 58500
    )
    assert r["algo_started"] is True


async def test_the_algo_starts_at_its_time_and_the_same_inputs_give_the_same_result(
    nifty_fut, store, tmp_path
):
    fut = nifty_fut.id
    late = await run_day(spec(fut, start="09:16"), DAY, iter(day_ticks(fut)), store, MODEL,
                         tmp_path / "a")  # fmt: skip
    assert [t["price_paise"] for t in late["trades"]] == [2500210, 2500510]  # 09:16:00, 09:20:00
    again = await run_day(spec(fut, start="09:16"), DAY, iter(day_ticks(fut)), store, MODEL,
                          tmp_path / "b")  # fmt: skip
    assert json.dumps(again, sort_keys=True) == json.dumps(late, sort_keys=True)  # to the byte
    never = await run_day(spec(fut, start="15:00"), DAY, iter(day_ticks(fut)), store, MODEL,
                          tmp_path / "c")  # fmt: skip
    assert never["algo_started"] is False and never["trades"] == []
    assert (await run_day(spec(fut), DAY, iter([]), store, MODEL, tmp_path))["skipped"]


def test_the_run_summary_drawdown_and_winning_days():
    def d(day, net):
        return {"day": day, "totals": {"net_paise": net, "gross_paise": net + 10,
                                       "charges_paise": 10, "trades": 1}}  # fmt: skip

    s = summarise([d("2026-10-12", 100), d("2026-10-13", -300), {"day": "2026-10-14",
                   "skipped": "nothing recorded"}, d("2026-10-15", 50)])  # fmt: skip
    assert s["equity_curve"] == [("2026-10-12", 100), ("2026-10-13", -200), ("2026-10-15", -150)]
    assert s["max_drawdown_paise"] == 300  # from +100 down to −200
    assert (s["winning_days"], s["win_rate"], s["days_run"]) == (2, 0.6667, 3)
    assert s["days_skipped"] == ["2026-10-14"] and s["net_paise"] == -150
    assert s["charges_paise"] == 30 and s["gross_paise"] == -120
    assert summarise([])["win_rate"] is None
    lost_to_charges = summarise([d("2026-10-12", -5)])  # gross +5, net −5: not a winning day
    assert (lost_to_charges["winning_days"], lost_to_charges["win_rate"]) == (0, 0.0)


def test_candles_become_ticks_adverse_path_first(nifty_fut):
    up = {"time": T0.isoformat(), "open": 2500000, "high": 2502000, "low": 2499000,
          "close": 2501000, "volume": 750}  # fmt: skip
    down = {**up, "time": (T0 + timedelta(minutes=1)).isoformat(), "close": 2499500}
    ticks = [t for _, t in candles_to_ticks(nifty_fut, [up, down])]
    up_path, down_path = [t.ltp_paise for t in ticks[:4]], [t.ltp_paise for t in ticks[4:]]
    assert up_path == [2500000, 2499000, 2502000, 2501000]  # a rising candle: its low first
    assert down_path == [2500000, 2502000, 2499000, 2499500]  # a falling one: its high first
    assert ticks[0].bids[0].price_paise == 2499990 and ticks[0].asks[0].price_paise == 2500010


def test_the_candle_store_checks_rows(tmp_path):
    cs = CandleStore(tmp_path)
    good = {"time": T0.isoformat(), "open": 100, "high": 110, "low": 90, "close": 105}
    assert cs.put("NFO:X", DAY, [good]) == 1 and cs.rows("NFO:X", DAY) == [good]
    with pytest.raises(BacktestError, match="row 1: low ≤ open, close ≤ high"):
        cs.put("NFO:X", DAY, [{**good, "high": 95}])
    with pytest.raises(BacktestError, match="must carry its zone and fall on 2026-10-16"):
        cs.put("NFO:X", DAY, [{**good, "time": "2026-10-16T09:15:00"}])
    with pytest.raises(BacktestError, match="row 1: needs time"):
        cs.put("NFO:X", DAY, [{"open": 1}])
    assert cs.rows("NFO:X", date(2026, 10, 17)) is None


async def test_runs_are_kept_and_a_run_cut_by_a_restart_says_so(nifty_fut, store, tmp_path):
    fut = nifty_fut.id
    recorded = {DAY: day_ticks(fut)}
    runs = Backtests(tmp_path, store, MODEL, ticks=lambda d: iter(recorded[d]) if d in recorded
                     else None)  # fmt: skip
    run = runs.start(spec(fut, days=(DAY.isoformat(), "2026-10-17")))
    await runs.settle()
    assert run.state == "done" and run.progress == 2
    assert run.result["summary"]["days_skipped"] == ["2026-10-17"]
    assert run.result["summary"]["net_paise"] == run.result["daily"][0]["totals"]["net_paise"]
    with pytest.raises(
        BacktestError, match="side must be BUY or SELL; unknown instrument NFO:NOPE"
    ):
        runs.start(spec("NFO:NOPE", side="HOLD"))
    with pytest.raises(BacktestError, match="no candles were imported"):
        runs.start(spec(fut), candles=True)
    with pytest.raises(BacktestError, match="unknown algo 'NOPE'"):  # refused up front
        runs.start(spec(fut, algo="NOPE"))
    bad = runs.start(spec(fut, params={"slices": 0, "interval_s": 60}))  # the engine says no
    await runs.settle()
    assert bad.state == "failed" and "the algo couldn't start" in bad.error, bad.error
    # a fresh server reads the runs back; one that was running when it stopped is failed
    (tmp_path / run.run_id / "run.json").write_text(
        json.dumps(
            {**json.loads((tmp_path / run.run_id / "run.json").read_text()), "state": "running"}
        )
    )
    again = Backtests(tmp_path, store, MODEL, ticks=lambda d: None)
    kept = again.get(run.run_id)
    assert kept.state == "failed" and kept.error == "the server stopped during the run"
    assert again.get(bad.run_id).state == "failed"


async def test_a_candle_based_run(nifty_fut, store, tmp_path):
    fut = nifty_fut.id
    cs = CandleStore(tmp_path / "candles")
    cs.put(fut, DAY, [{"time": (T0 + timedelta(minutes=m)).isoformat(), "open": 2500000 + m * 100,
                       "high": 2500300 + m * 100, "low": 2499800 + m * 100,
                       "close": 2500200 + m * 100} for m in range(5)])  # fmt: skip
    runs = Backtests(tmp_path / "runs", store, MODEL, ticks=lambda d: None, candles=cs.rows)
    run = runs.start(spec(fut), candles=True)
    await runs.settle()
    assert run.state == "done" and run.to_dict()["candle_based"] is True
    trades = run.result["daily"][0]["trades"]
    assert [t["price_paise"] for t in trades] == [2500010, 2500110]  # the ask: open + one tick


async def test_the_backtest_api(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    c = await w.logged_in()
    assert (await c.get("/api/research/backtests")).status_code == 404
    fut = nifty_fut.id
    runs = Backtests(tmp_path / "bt", w.platform.instruments, MODEL,
                     ticks=lambda d: iter(day_ticks(fut)) if d == DAY else None)  # fmt: skip
    cs = CandleStore(tmp_path / "candles")
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), backtests=runs,
                       candles=cs)  # fmt: skip
    c = await w.logged_in()
    body = {"days": [DAY.isoformat()], "algo": "SLICER", "instrument_id": fut, "side": "BUY",
            "qty": 150, "params": {"slices": 2, "interval_s": 60, "mode": "cross"}}  # fmt: skip
    r = await c.post("/api/research/backtests", json=body)
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    await runs.settle()
    listing = (await c.get("/api/research/backtests")).json()
    assert listing[0]["run_id"] == run_id and "daily" not in listing[0]
    got = (await c.get(f"/api/research/backtests/{run_id}")).json()
    assert got["state"] == "done" and got["summary"]["trades"] == 2 and len(got["daily"]) == 1
    bad = await c.post("/api/research/backtests", json={**body, "days": ["16-10-2026"]})
    assert bad.status_code == 422 and "not a date" in bad.json()["detail"]
    assert (await c.get("/api/research/backtests/BTnope")).status_code == 404
    rows = [{"time": T0.isoformat(), "open": 100, "high": 110, "low": 90, "close": 105}]
    r = await c.post("/api/research/candles", json={"instrument_id": fut, "day": "2026-10-16",
                                                     "rows": rows})  # fmt: skip
    assert r.status_code == 201 and r.json()["rows"] == 1
    await c.aclose()
    w.log.close()
    await asyncio.sleep(0)


async def test_the_screens_api_form_and_a_day_as_csv_and_pdf(tmp_path, nifty_fut):
    """K11.4: the server publishes the form (algos, days, products, defaults) and hands out
    each run's day as the K10.3 report: JSON, CSV or PDF; a skipped day says why."""
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.research.ticks import FileTickStore
    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    fut = nifty_fut.id
    recorded = FileTickStore(tmp_path / "ticks")
    for at in (T0 - timedelta(days=1), T0):
        recorded.append([(book(fut, 2500000), at)])
    runs = Backtests(tmp_path / "bt", w.platform.instruments, MODEL,
                     ticks=lambda d: iter(day_ticks(fut)) if d == DAY else None)  # fmt: skip
    cs = CandleStore(tmp_path / "candles")
    rows = [{"time": T0.isoformat(), "open": 100, "high": 110, "low": 90, "close": 105}]
    cs.put(fut, DAY, rows)
    cs.put(
        fut, DAY - timedelta(days=3), [{**rows[0], "time": (T0 - timedelta(days=3)).isoformat()}]
    )
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), backtests=runs,
                       candles=cs, ticks=recorded)  # fmt: skip
    c = await w.logged_in()
    form = (await c.get(f"/api/research/backtests/form?instrument_id={fut}")).json()
    assert [a["code"] for a in form["algos"]][:2] == ["SLICER", "TWAP"]
    assert form["algos"][0]["params"]  # each algo's settings, for the screen to draw
    assert form["days"] == ["2026-10-16", "2026-10-15"]  # newest first
    assert form["products"] == ["CNC", "MIS", "NRML"] and form["candles"] is True
    assert form["defaults"] == {"product": "NRML", "start": "09:15", "capital_paise": 10**8}
    assert form["candle_days"] == ["2026-10-16", "2026-10-13"] and form["max_days"] == 250
    assert "candle_days" not in (await c.get("/api/research/backtests/form")).json()

    body = {"days": ["2026-10-15", DAY.isoformat()], "algo": "SLICER", "instrument_id": fut,
            "side": "BUY", "qty": 150,
            "params": {"slices": 2, "interval_s": 60, "mode": "cross"}}  # fmt: skip
    bad = await c.post("/api/research/backtests", json={**body, "algo": "NOPE"})
    assert bad.status_code == 422 and "unknown algo 'NOPE'" in bad.json()["detail"]
    run_id = (await c.post("/api/research/backtests", json=body)).json()["run_id"]
    early = await c.get(f"/api/research/backtests/{run_id}/days/{DAY}")
    assert early.status_code == 404 and "hasn't finished" in early.json()["detail"]
    await runs.settle()
    base = f"/api/research/backtests/{run_id}/days"
    day = (await c.get(f"{base}/{DAY}")).json()
    assert day["day"] == "2026-10-16" and day["totals"]["trades"] == 2 and day["equity_curve"]
    csv = await c.get(f"{base}/{DAY}?format=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert f'filename="mocktrade-backtest-{run_id}-2026-10-16.csv"' in csv.headers[
        "content-disposition"]  # fmt: skip
    assert "NIFTY26OCTFUT" in csv.text
    pdf = await c.get(f"{base}/{DAY}?format=pdf")
    assert pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    skipped = await c.get(f"{base}/2026-10-15")
    assert skipped.status_code == 404 and "skipped: nothing recorded" in skipped.json()["detail"]
    other = await c.get(f"{base}/2026-10-14")
    assert other.status_code == 404 and "isn't one of the days" in other.json()["detail"]
    assert (await c.get(f"{base}/{DAY}?format=xls")).status_code == 422
    assert (await c.get(f"/api/research/backtests/BTnope/days/{DAY}")).status_code == 404
    await c.aclose()
    w.log.close()
    await asyncio.sleep(0)
