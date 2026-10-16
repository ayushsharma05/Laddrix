"""Daily operation: the scheduler with a fake clock, alerts, the checks and the webhook."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.ticks import Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.ops.alerts import Alert, Alerts
from mocktrade.ops.checks import PRICES_SILENT_S, Checks
from mocktrade.ops.notify import WebhookNotifier
from mocktrade.ops.scheduler import IST, Scheduler, parse_holidays

WED = date(2026, 10, 14)  # a Wednesday


def ist(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST).astimezone(UTC)


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def walk(s: Scheduler, clock: Clock, until: datetime, step=timedelta(seconds=1)):
    while clock.now < until:
        await s.run_due()
        clock.now += step


# --- the scheduler ---


async def test_a_daily_job_runs_once_at_its_india_time_on_trading_days_only():
    clock = Clock(ist(WED, 8, 0))
    ran = []
    s = Scheduler(clock=clock, holidays=frozenset({WED + timedelta(days=1)}))
    s.daily("08:30", lambda: ran.append(clock.now.astimezone(IST)))
    await walk(s, clock, ist(WED + timedelta(days=4), 9, 0), timedelta(minutes=1))
    # Wed yes, Thu holiday, Fri yes, Sat and Sun no
    assert [(d.date(), d.hour, d.minute) for d in ran] == [
        (WED, 8, 30),
        (date(2026, 10, 16), 8, 30),
    ]


async def test_a_daily_job_missed_by_more_than_its_grace_is_skipped_not_run_late():
    clock = Clock(ist(WED, 8, 0))
    ran = []
    s = Scheduler(clock=clock)
    s.daily("08:30", lambda: ran.append("job"))
    clock.now = ist(WED, 11, 0)  # the server was down (or asleep) all morning
    await s.run_due()
    assert ran == []
    clock.now = ist(WED, 8, 35) + timedelta(days=1)  # next day, 5 min late: within grace
    await s.run_due()
    assert ran == ["job"]


async def test_an_interval_job_runs_only_in_its_window_and_a_failing_job_hurts_nobody():
    clock = Clock(ist(WED, 9, 0))
    calls = []

    async def failing():
        raise RuntimeError("boom")

    s = Scheduler(clock=clock)
    s.every(15, failing, between=("09:15", "09:16"), name="failing")
    s.every(15, lambda: calls.append(clock.now.astimezone(IST).time()), between=("09:15", "09:16"))
    await walk(s, clock, ist(WED, 9, 20))
    assert [t.isoformat() for t in calls] == ["09:15:00", "09:15:15", "09:15:30", "09:15:45"]
    assert [name for name, _ in s.ran].count("failing") == 4


async def test_a_safety_warning_job_runs_on_weekends_too_others_do_not():
    sat = WED + timedelta(days=3)
    clock = Clock(ist(sat, 10, 0))
    warnings, trading = [], []
    s = Scheduler(clock=clock)
    s.every(5, lambda: warnings.append(1), between=("00:00", "23:59"), any_day=True)
    s.every(5, lambda: trading.append(1), between=("00:00", "23:59"))
    s.daily("10:00", lambda: trading.append("daily"))
    await walk(s, clock, ist(sat, 10, 0, 20))
    assert len(warnings) == 4 and trading == []  # a lost stop is shown on a Saturday too


def test_holidays_parse_and_reject_nonsense():
    assert parse_holidays("2026-10-21, 2026-11-10,") == {date(2026, 10, 21), date(2026, 11, 10)}
    with pytest.raises(ValueError):
        parse_holidays("21/10/2026")


# --- alerts ---


def test_an_alert_is_told_once_until_it_changes_and_its_end_is_told_too():
    told = []
    alerts = Alerts(clock=lambda: datetime(2026, 10, 14, tzinfo=UTC))
    alerts.subscribe(lambda a: told.append((a.key, a.level, a.active)))
    alerts.subscribe(lambda a: 1 / 0)  # a broken listener never stops the others
    alerts.raise_("prices", "WARN", "slow")
    alerts.raise_("prices", "WARN", "slow")  # again, the same: no storm
    alerts.raise_("prices", "ERROR", "stopped")  # worse: told again
    alerts.clear("disk")  # never raised: nothing to tell
    alerts.clear("prices")
    assert told == [("prices", "WARN", True), ("prices", "ERROR", True), ("prices", "OK", False)]
    assert alerts.active() == []
    with pytest.raises(ValueError):
        alerts.raise_("x", "OK", "not a level for raising")


# --- the checks ---

KITE = BrokerAccount("acc-ayush", "kite", "AB1234", "ayush")
PAPER = BrokerAccount("acc-paper", "paper", "P1", "ayush")


def checks(clock, status=None, md=None, expected=lambda: True, free=10 << 30):
    alerts = Alerts(clock=clock)
    c = Checks(
        alerts,
        md or MarketData(),
        [KITE, PAPER],
        Path("."),
        broker_status=status,
        prices_expected=expected,
        clock=clock,
        disk_free=lambda _p: free,
    )
    return c, alerts


def test_the_kite_login_reminder_escalates_towards_the_open_and_clears_on_login():
    clock = Clock(ist(WED, 8, 30))
    state = {"acc-ayush": "login_needed", "acc-paper": None}
    c, alerts = checks(clock, status=lambda a: state[a.id])
    c.kite_login()
    (a,) = alerts.active()
    assert (a.key, a.level) == ("broker_login:acc-ayush", "WARN")
    assert a.text == "Log in to Kite for acc-ayush (AB1234): the market opens at 09:15"
    clock.now = ist(WED, 9, 6)
    c.kite_login()
    assert alerts.active()[0].level == "ERROR"
    assert alerts.active()[0].text.endswith("the market opens in 9 min")
    clock.now = ist(WED, 9, 20)
    c.kite_login()
    assert alerts.active()[0].text.endswith("the market is open and this account can't trade")
    state["acc-ayush"] = "live"
    c.kite_login()
    assert alerts.active() == []


def test_silent_prices_raise_an_alert_only_when_a_feed_is_expected():
    clock = Clock(ist(WED, 10, 0))
    md = MarketData()
    expected = {"yes": True}
    c, alerts = checks(clock, md=md, expected=lambda: expected["yes"])
    md.on_tick(Tick("NSE:A", 100))
    c.prices()
    clock.now += timedelta(seconds=PRICES_SILENT_S - 1)
    c.prices()
    assert alerts.active() == []
    clock.now += timedelta(seconds=2)
    c.prices()
    assert alerts.active()[0].key == "prices"
    md.on_tick(Tick("NSE:A", 101))
    c.prices()
    assert alerts.active() == []
    expected["yes"] = False  # e.g. nobody logged in to Kite yet: silence is expected
    clock.now += timedelta(minutes=10)
    c.prices()
    assert alerts.active() == []


@pytest.mark.parametrize(
    "free, level", [(10 << 30, None), (900 << 20, "WARN"), (100 << 20, "ERROR")]
)
def test_disk_space(free, level):
    c, alerts = checks(Clock(ist(WED, 10, 0)), free=free)
    c.disk()
    assert [a.level for a in alerts.active()] == ([level] if level else [])


# --- the webhook ---


async def test_the_webhook_gets_warnings_and_their_all_clear_but_not_info():
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200)

    notifier = WebhookNotifier(
        "https://hooks.example/x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    alerts = Alerts()
    alerts.subscribe(notifier)
    alerts.raise_("info", "INFO", "just so you know")
    alerts.clear("info")  # its start wasn't sent, so its end isn't either
    alerts.raise_("prices", "ERROR", "No prices")
    alerts.clear("prices", "prices are flowing again")
    await notifier.aclose()
    assert posted == [
        {"text": "🔴 MockTrade: No prices", "level": "ERROR", "key": "prices"},
        {"text": "🟢 MockTrade: prices are flowing again", "level": "OK", "key": "prices"},
    ]


async def test_a_failing_webhook_is_only_logged(caplog):
    def handler(request):
        raise httpx.ConnectError("down")

    notifier = WebhookNotifier(
        "https://hooks.example/x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    notifier(Alert("k", "ERROR", "t", True, datetime.now(UTC)))
    await notifier.aclose()
    assert "alert webhook failed" in caplog.text


async def test_a_whole_trading_day_with_the_login_at_9_02():
    clock = Clock(ist(WED, 6, 0))
    state = {"acc-ayush": "login_needed", "acc-paper": None}
    md = MarketData()
    c, alerts = checks(
        clock, status=lambda a: state[a.id], md=md, expected=lambda: state["acc-ayush"] == "live"
    )
    timeline = []
    alerts.subscribe(
        lambda a: timeline.append((clock.now.astimezone(IST).strftime("%H:%M"), a.key, a.level))
    )
    refreshed = []
    s = Scheduler(clock=clock)
    s.daily("08:30", lambda: refreshed.append(clock.now), "instrument refresh")
    s.every(60, c.kite_login, between=("08:30", "15:30"))
    s.every(15, c.prices, between=("09:16", "15:30"))
    while clock.now < ist(WED, 16, 0):
        if clock.now >= ist(WED, 9, 2):
            state["acc-ayush"] = "live"
        if state["acc-ayush"] == "live" and clock.now.second == 0:
            md.on_tick(Tick("NSE:A", 100 + clock.now.minute))  # Kite streams once logged in
        await s.run_due()
        clock.now += timedelta(seconds=5)
    assert len(refreshed) == 1
    assert timeline == [
        ("08:30", "broker_login:acc-ayush", "WARN"),  # the reminder
        ("09:02", "broker_login:acc-ayush", "OK"),  # logged in: cleared, nothing else all day
    ]
