"""End-of-day reports and P&L history, checked against hand-worked days (K10.3, #215).

Two days of NIFTY futures (lot 75), worked by hand:

    Mon 12 Oct  buy 75 @ 25,000 (manual), buy 75 @ 25,020 (basket B1); closes at 25,050
                → long 150 at an average of 25,010; unrealised 150 × 40 = ₹6,000
                  manual 75 × 50 = ₹3,750, basket B1 75 × 30 = ₹2,250 (adds up to ₹6,000)
    Tue 13 Oct  150 carried in at Monday's close 25,050; sell 150 @ 25,100 (manual)
                → realised 150 × 50 = ₹7,500
    The two days add up to what the trades made: 75 × 100 + 75 × 80 = ₹13,500.
"""

import asyncio
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from mocktrade.charges.model import ScheduleCharges, executed
from mocktrade.charges.schedules import KITE_STANDARD
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.domain.ticks import Tick
from mocktrade.orders.model import EventKind, Fill, OrderEvent, OrderView, Pending
from mocktrade.reports.daily import build_day, report_to_dict
from mocktrade.reports.export import report_csv, report_lines, report_pdf, rupees
from mocktrade.reports.history import pnl_history
from mocktrade.reports.pdf import LINES_PER_PAGE
from mocktrade.reports.store import ReportStore
from mocktrade.store.events import EventLog

IST = ZoneInfo("Asia/Kolkata")
FUT = "NFO:NIFTY26OCTFUT"
MON, TUE, WED = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)
MODEL = ScheduleCharges(KITE_STANDARD)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(UTC)


def fill(log, cid, side, qty, rupees_, when, basket=None, fills=None):
    """One order filling in the given parts [(qty, price), ...] at `when`."""
    request = OrderRequest(cid, "acc", "ayush", FUT, side, qty, OrderType.LIMIT, Product.NRML,
                           price_paise=rupees_ * 100, basket_id=basket)  # fmt: skip
    done = 0
    notional = 0
    for part, price in fills or [(qty, rupees_ * 100)]:
        done += part
        notional += part * price
        status = OrderStatus.FILLED if done == qty else OrderStatus.PARTIAL
        view = OrderView(request, f"B{cid}", status, done, notional // done, "", Pending.NONE,
                         when, when)  # fmt: skip
        log.record(OrderEvent(EventKind.FILL, when, "acc", cid, view,
                              fill=Fill(cid, part, price, True)))  # fmt: skip


def rejected(log, cid, when, reason):
    request = OrderRequest(cid, "acc", "ayush", FUT, Side.BUY, 750, OrderType.LIMIT,
                           Product.NRML, price_paise=2510000)  # fmt: skip
    view = OrderView(request, None, OrderStatus.REJECTED, 0, 0, reason, Pending.NONE, when, when)
    log.record(OrderEvent(EventKind.REJECTED, when, "acc", cid, view, detail=reason))


def prices(ltp, close):
    return lambda i: Tick(i, ltp, close_paise=close) if i == FUT else None


@pytest.fixture
async def log(tmp_path, nifty_fut):
    events = EventLog(tmp_path / "events.db")
    events.start()
    fill(events, "M1", Side.BUY, 75, 25000, at(MON, 10))
    fill(events, "M2", Side.BUY, 75, 25020, at(MON, 11), basket="B1")
    fill(events, "T1", Side.SELL, 150, 25100, at(TUE, 10),
         fills=[(75, 2510000), (75, 2510000)])  # fmt: skip  # two fills, one order
    rejected(events, "T2", at(TUE, 10, 5), "position limit: at most 20 lots")
    events.record_note("POSITION_MISMATCH_RAISED", "acc", "MockTrade shows +150, the broker 0",
                       ts=at(TUE, 10, 6))  # fmt: skip
    events.record_note("ALGO_STARTED", "acc", "not an alert", ts=at(TUE, 10, 7))
    await events.flush()
    yield events
    await events.aclose()


def instruments(nifty_fut):
    return {FUT: nifty_fut}.get


def day(log, nifty_fut, d, ltp=None, close=None, final=True):
    p = prices(ltp, close) if ltp else None
    return build_day("acc", d, log, instruments(nifty_fut), MODEL, prices=p,
                     now=at(d, 16), final=final)  # fmt: skip


async def test_monday_open_position_at_the_close_and_by_strategy(log, nifty_fut):
    r = day(log, nifty_fut, MON, ltp=2505000, close=2499000)
    (p,) = r.positions
    assert (p.carried_qty, p.buy_qty, p.sell_qty, p.closing_qty) == (0, 150, 0, 150)
    assert (p.realised_paise, p.unrealised_paise) == (0, 150 * 4000)  # ₹6,000
    assert r.priced and r.final and r.notes == ()
    strategies = {s.strategy: (s.trades, s.gross_paise) for s in r.strategies}
    assert strategies == {"manual": (1, 75 * 5000), "basket B1": (1, 75 * 3000)}
    assert sum(s.gross_paise for s in r.strategies) == r.gross_paise
    # charges: exactly the charges model's bill for Monday's two executed orders
    views = log.latest_views("acc", since=at(MON, 0))
    monday = [v for v in views if v.created_at < at(TUE, 0)]
    expected = MODEL.bill(executed(monday, instruments(nifty_fut)))
    assert r.charges_paise == expected.total_paise and p.charges_paise == expected.total_paise
    assert r.net_paise == r.gross_paise - r.charges_paise
    assert {s.strategy: s.charges_paise for s in r.strategies}["basket B1"] > 0
    assert r.carried_forward == (p,) and r.trades[0].strategy == "manual"


async def test_tuesday_carried_in_at_mondays_close_and_the_days_add_up(log, nifty_fut):
    mon = day(log, nifty_fut, MON, ltp=2505000, close=2499000)
    tue = day(log, nifty_fut, TUE, ltp=2510000, close=2505000)  # Tuesday's previous close
    (p,) = tue.positions
    assert (p.carried_qty, p.reference_paise, p.sell_qty, p.closing_qty) == (150, 2505000, 150, 0)
    assert (p.realised_paise, p.unrealised_paise) == (150 * 5000, 0)  # ₹7,500
    assert mon.gross_paise + tue.gross_paise == 75 * 10000 + 75 * 8000  # ₹13,500
    assert [(t.client_order_id, t.qty) for t in tue.trades] == [
        ("T1", 75),
        ("T1", 75),
    ]  # a row per fill
    assert {s.strategy for s in tue.strategies} == {"carried", "manual"}
    assert sum(s.gross_paise for s in tue.strategies) == tue.gross_paise
    assert [n.detail for n in tue.rejects] == [
        "T2 BUY 750 NFO:NIFTY26OCTFUT: position limit: at most 20 lots"]  # fmt: skip
    assert [n.kind for n in tue.alerts] == ["POSITION_MISMATCH_RAISED"]  # not the algo note
    assert tue.carried_forward == ()


async def test_a_day_rebuilt_without_prices_says_so(log, nifty_fut):
    tue = day(log, nifty_fut, TUE, final=False)
    (p,) = tue.positions
    assert p.reference_paise == 2501000  # at cost: the average 25,010
    assert p.realised_paise == 150 * 9000  # ₹13,500 against the cost
    assert not tue.priced and "valued at their cost" in tue.notes[0]
    mon = day(log, nifty_fut, MON, final=False)
    assert mon.unrealised_paise == 0 and "no last price" in mon.notes[0]
    assert day(log, nifty_fut, WED, ltp=2510000, close=2510000).positions == ()


async def test_history_by_day_instrument_and_strategy_from_kept_reports(log, nifty_fut, tmp_path):
    store = ReportStore(tmp_path / "reports")
    store.save(report_to_dict(day(log, nifty_fut, MON, ltp=2505000, close=2499000)))
    store.save(report_to_dict(day(log, nifty_fut, TUE, ltp=2510000, close=2505000)))
    assert store.days("acc") == [MON, TUE]
    kept = store.between("acc", MON, WED)
    by_day = pnl_history(kept, "day", MON, WED)
    assert [(r["key"], r["gross_paise"]) for r in by_day["rows"]] == [
        ("2026-10-12", 600000), ("2026-10-13", 750000)]  # fmt: skip
    assert by_day["total"]["gross_paise"] == 1350000
    assert by_day["missing_days"] == ["2026-10-14"]  # never silently zero
    by_instrument = pnl_history(kept, "instrument", MON, WED)
    (row,) = by_instrument["rows"]
    assert (row["key"], row["gross_paise"], row["trades"]) == (FUT, 1350000, 4)  # fills
    assert row["net_paise"] == row["gross_paise"] - row["charges_paise"]
    by_strategy = {
        r["key"]: r["gross_paise"] for r in pnl_history(kept, "strategy", MON, WED)["rows"]
    }
    assert by_strategy == {"basket B1": 225000, "carried": 750000, "manual": 375000}
    with pytest.raises(ValueError, match="by must be one of"):
        pnl_history(kept, "week", MON, WED)
    with pytest.raises(ValueError, match="not a safe account id"):
        store.days("../etc")


async def test_csv_and_pdf_say_the_same_as_the_report(log, nifty_fut):
    data = report_to_dict(day(log, nifty_fut, TUE, ltp=2510000, close=2505000))
    text = report_csv(data)
    assert "Gross P&L,7500.00" in text
    assert "NFO:NIFTY26OCTFUT,NRML,150,25050.00,0,0.00,150,3765000.00,0" in text
    assert "position limit: at most 20 lots" in text and "POSITION_MISMATCH_RAISED" in text
    assert rupees(-5) == "-0.05" and rupees(None) == ""
    lines = report_lines(data)
    assert any(line.startswith("Net P&L") for line in lines)
    pdf = report_pdf(data)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/Count 1" in pdf and b"Gross P&L" in pdf
    many = {**data, "trades": [data["trades"][0]] * 200}
    pages = -(-len(report_lines(many)) // LINES_PER_PAGE)  # rounded up
    assert pages > 3 and b"/Count %d" % pages in report_pdf(many)  # 200 trades, several pages
    assert b"Rs " in report_pdf({**data, "schedule": "₹ (test)"})  # no rupee sign in Courier
    assert b"\\(test\\)" in report_pdf({**data, "schedule": "₹ (test)"})  # brackets escaped


# --- the job and the API -----------------------------------------------------------------------


async def test_the_close_of_day_job_keeps_a_report_and_the_api_serves_it(tmp_path, nifty_fut):
    from test_server import ORIGIN
    from test_server import World as ServerWorld

    from mocktrade.reports.service import Reports
    from mocktrade.server.app import ServerSettings, create_app

    w = ServerWorld(tmp_path, nifty_fut)
    store = ReportStore(tmp_path / "reports")
    today = datetime.now(UTC).astimezone(IST).date()
    reports = Reports(w.platform, store)
    await reports.close_of_day()
    assert store.days("acc-ayush") == [today]
    await w.platform.event_log.flush()
    kinds = [e.kind for e in w.platform.event_log.events(account_id="acc-ayush")]
    assert "REPORT_KEPT" in kinds

    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), reports=reports)
    c = await w.logged_in()
    listing = (await c.get("/api/accounts/acc-ayush/reports")).json()
    assert listing == {"today": today.isoformat(), "kept": [today.isoformat()]}
    r = await c.get(f"/api/accounts/acc-ayush/reports/{today}")
    assert r.status_code == 200 and r.json()["final"] is True
    csv_ = await c.get(f"/api/accounts/acc-ayush/reports/{today}?format=csv")
    assert csv_.headers["content-type"].startswith("text/csv")
    assert f'filename="mocktrade-acc-ayush-{today}.csv"' in csv_.headers["content-disposition"]
    pdf = await c.get(f"/api/accounts/acc-ayush/reports/{today}?format=pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert (await c.get("/api/accounts/acc-ayush/reports/2099-01-01")).status_code == 422
    assert (await c.get("/api/accounts/acc-ayush/reports/yesterday")).status_code == 422
    assert (await c.get(f"/api/accounts/acc-ayush/reports/{today}?format=xls")).status_code == 422
    pnl = (await c.get("/api/accounts/acc-ayush/pnl?by=day")).json()
    assert pnl["by"] == "day" and [r["key"] for r in pnl["rows"]] == [today.isoformat()]
    assert (await c.get("/api/accounts/acc-ayush/pnl?by=week")).status_code == 422
    assert (await c.get("/api/accounts/acc-nobody/reports")).status_code == 404
    await c.aclose()
    w.log.close()
    await asyncio.sleep(0)
