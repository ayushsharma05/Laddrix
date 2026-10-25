"""Operations (#287): JSON logs with ids, one status of every part, readiness from it, the
status page for admin and risk, and metrics a monitoring system can read with its own key.

The gaps before:
- logs were plain text: one order's journey couldn't be picked out of the server's log;
- readiness knew only the loops and recovery: an audit log that can't write, or no prices in
  market hours, still said ready;
- no page said which part was wrong, since when;
- /api/metrics needed a signed-in admin: a monitoring system couldn't read it, and its
  Prometheus text said each labelled metric's TYPE more than once, which Prometheus refuses.
"""

import asyncio
import io
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from test_order_manager import req
from test_server import ORIGIN, World

from laddrix.broker.fake import FakeBroker
from laddrix.logsafe import Redact
from laddrix.ops import logs
from laddrix.ops.alerts import Alerts
from laddrix.ops.status import Status, worst
from laddrix.ops.supervisor import Supervisor
from laddrix.orders.manager import OrderManager
from laddrix.orders.model import ClientIds
from laddrix.recovery import RecoveryGate
from laddrix.refdata.store import InstrumentStore
from laddrix.server.app import ServerSettings, create_app
from laddrix.tools.serve import ConfigError, ServeSettings

T0 = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)
KEY = "k" * 40


@pytest.fixture
def captured():
    """Every log line, as the server writes it (JSON, through the secret filter)."""
    root = logging.getLogger()
    before = list(root.handlers), root.level
    out = io.StringIO()
    handler = logs.setup("json", stream=out)
    handler.addFilter(Redact())
    yield lambda: [json.loads(line) for line in out.getvalue().splitlines()]
    root.handlers[:], root.level = before[0], before[1]


# --- logs ---------------------------------------------------------------------------------------


def test_a_line_carries_the_ids_bound_while_it_was_logged(captured):
    log = logging.getLogger("laddrix.test")
    with logs.bound(account_id="acc-1", client_order_id="MT7", broker_order_id=None):
        log.warning("place %s: no answer", "MT7")
    log.info("after", extra={"user": "ayush"})
    first, second = captured()
    assert first["msg"] == "place MT7: no answer" and first["level"] == "WARNING"
    assert (first["account_id"], first["client_order_id"]) == ("acc-1", "MT7")
    assert "broker_order_id" not in first  # None is left out, not written as null
    assert first["logger"] == "laddrix.test" and first["ts"].endswith("Z")
    assert "account_id" not in second and second["user"] == "ayush"  # unbound after the block


def test_an_error_keeps_its_traceback_and_secrets_stay_hidden(captured):
    try:
        raise ValueError("bad https://hooks.example.com/services/T0/B0/secretpart")
    except ValueError:
        logging.getLogger("laddrix.test").exception("login with password=hunter2 failed")
    (line,) = captured()
    assert "hunter2" not in json.dumps(line) and "secretpart" not in json.dumps(line)
    assert "password=[redacted]" in line["msg"] and "ValueError" in line["error"]


def test_a_request_id_is_the_callers_only_when_plain():
    assert logs.start_request("abc-123.X_y") == "abc-123.X_y"
    made = logs.start_request("not plain\nlog injection")
    assert len(made) == 16 and made.isalnum() and logs.ids() == {"request_id": made}
    assert logs.start_request("x" * 65) != "x" * 65
    logs.note(user="riya")
    assert logs.ids() == {"request_id": logs.ids()["request_id"], "user": "riya"}


async def test_an_orders_log_lines_carry_its_account_and_id(captured, nifty_fut):
    class Silent(FakeBroker):
        async def place(self, request):
            raise TimeoutError("no answer")

    store = InstrumentStore()
    store.add(nifty_fut)
    om = OrderManager("acc-1", Silent(), store.get, ids=ClientIds("MT"),
                      clock=lambda: T0)  # fmt: skip
    view = await om.submit(req())
    line = next(x for x in captured() if "no clear answer" in x["msg"])
    assert (line["account_id"], line["client_order_id"]) == ("acc-1", view.client_order_id)
    assert logs.ids().get("client_order_id") is None  # not left behind for the next line


def test_the_log_format_setting(tmp_path):
    env = {"LADDRIX_SECRET_KEY": _key(), "LADDRIX_ORIGIN": "https://t.example.in"}
    assert ServeSettings.from_env(env).log_format == "json"  # the default
    assert ServeSettings.from_env({**env, "LADDRIX_LOG_FORMAT": "text"}).log_format == "text"
    with pytest.raises(ConfigError, match="LADDRIX_LOG_FORMAT must be json or text"):
        ServeSettings.from_env({**env, "LADDRIX_LOG_FORMAT": "xml"})
    with pytest.raises(ConfigError, match="LADDRIX_METRICS_TOKEN must be at least 32"):
        ServeSettings.from_env({**env, "LADDRIX_METRICS_TOKEN": "short"})
    made = ServeSettings.from_env({**env, "LADDRIX_METRICS_TOKEN": KEY})
    assert made.metrics_token == KEY and KEY not in repr(made)


def _key():
    from laddrix.server.security import SecretBox

    return SecretBox.new_key()


# --- the status of each part --------------------------------------------------------------------


class Runtime:
    def __init__(self, gate=None):
        self.recovery = gate


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def status_of(accounts=None, alerts=None, problem=None, ticking=True):
    clock = Clock()
    sup = Supervisor(monotonic=lambda: 100.0, clock=clock)
    if ticking:
        sup._beat = 100.0  # its watchdog ticked just now
    seen = {"problem": problem}
    status = Status(sup, lambda: accounts or {}, alerts, lambda: seen["problem"], clock=clock)
    return status, sup, clock, seen


def by_name(status):
    return {p.name: p for p in status.parts()}


def test_all_well_is_ready_and_says_each_part():
    status, *_ = status_of()
    parts = by_name(status)
    assert list(parts) == ["server", "supervisor", "audit log", "prices", "broker login",
                           "disk", "price load"]  # fmt: skip
    assert {p.state for p in parts.values()} == {"ready"} and status.ready() == (True, [])
    assert parts["prices"].reason == "no problem reported"


def test_a_stuck_watchdog_or_audit_log_is_down_and_not_ready():
    status, *_ = status_of(ticking=False)
    assert by_name(status)["supervisor"].state == "down" and not status.ready()[0]
    status, _, _, seen = status_of(problem="the disk is full")
    part = by_name(status)["audit log"]
    assert (part.state, part.reason) == ("down", "the disk is full: new orders are refused")
    seen["problem"] = None
    assert status.ready()[0]


async def test_loops_critical_ones_down_side_ones_degraded():
    status, sup, *_ = status_of()

    async def fails():
        raise RuntimeError("boom")

    async def never():  # the restart pause: it stays restarting
        await asyncio.Event().wait()

    sup._sleep = lambda _s: never()
    a = asyncio.create_task(sup.keep("fills acc-1", fails, account_id="acc-1"))
    b = asyncio.create_task(sup.keep("portfolio", fails, critical=False))
    await asyncio.sleep(0)
    parts = by_name(status)
    fills, side = parts["loop fills acc-1"], parts["loop portfolio"]
    assert (fills.state, fills.account_id) == ("down", "acc-1")
    assert fills.reason == "restarting after a failure (RuntimeError: boom); new orders paused"
    assert side.state == "degraded" and "paused" not in side.reason
    assert fills.since == T0
    assert [p.name for p in status.ready()[1]] == ["loop fills acc-1"]
    for t in (a, b):
        t.cancel()
    await asyncio.gather(a, b, return_exceptions=True)


def test_recovery_starting_then_ready_or_down():
    gate = RecoveryGate()
    status, *_ = status_of(accounts={"acc-1": Runtime(gate), "acc-2": Runtime()})
    part = by_name(status)["recovery acc-1"]
    assert (part.state, part.account_id) == ("starting", "acc-1") and not status.ready()[0]
    assert "recovery acc-2" not in by_name(status)  # no recovery to do: no row
    gate.set("failed", "the broker refused the order list")
    part = by_name(status)["recovery acc-1"]
    assert (part.state, part.reason) == ("down", "the broker refused the order list")
    gate.set("ok")
    assert by_name(status)["recovery acc-1"].state == "ready" and status.ready()[0]


def test_alerts_become_parts_only_the_servers_prices_and_disk_errors_are_down():
    alerts = Alerts(clock=lambda: T0 - timedelta(minutes=3))
    status, *_ = status_of(alerts=alerts)
    alerts.raise_("broker_login:acc-1", "ERROR", "Log in to Kite for acc-1", account_id="acc-1")
    alerts.raise_("prices:acc-2", "ERROR", "acc-2: No prices", account_id="acc-2")
    alerts.raise_("disk", "WARN", "Data disk low: 900 MB free")
    alerts.raise_("audit_log", "ERROR", "shown by its own part")
    alerts.raise_("loop:fills", "ERROR", "shown by its own part")
    parts = by_name(status)
    assert parts["broker login acc-1"].state == "degraded"  # never stops the screens
    assert parts["prices acc-2"].state == "degraded"  # one firm's own feed
    assert parts["disk"].state == "degraded" and parts["disk"].since == T0 - timedelta(minutes=3)
    assert "broker login" not in parts and "audit_log" not in parts and "loop" not in parts
    assert status.ready()[0]
    alerts.raise_("disk", "ERROR", "Data disk almost full: 150 MB free")
    alerts.raise_("prices", "ERROR", "No prices for over a minute")
    alerts.raise_("replay", "WARN", "REPLAY of 2026-10-23")
    parts = by_name(status)
    assert (parts["disk"].state, parts["prices"].state) == ("down", "down")
    assert parts["replay"].state == "degraded"
    assert worst(parts.values()) == "down" and not status.ready()[0]


def test_since_is_when_a_part_entered_its_state():
    status, _, clock, seen = status_of()
    assert by_name(status)["audit log"].since == T0
    clock.now = T0 + timedelta(seconds=30)
    assert by_name(status)["audit log"].since == T0  # still the same state: the same time
    seen["problem"] = "can't write"
    assert by_name(status)["audit log"].since == T0 + timedelta(seconds=30)


async def test_watch_notes_a_change_when_nobody_is_looking():
    status, _, clock, seen = status_of()
    watching = asyncio.create_task(status.watch(every_s=0))
    await asyncio.sleep(0)
    clock.now = T0 + timedelta(seconds=7)
    seen["problem"] = "can't write"
    await asyncio.sleep(0)  # one look, by itself
    clock.now = T0 + timedelta(minutes=5)  # asked much later: the time it was first seen
    assert by_name(status)["audit log"].since == T0 + timedelta(seconds=7)
    watching.cancel()
    await asyncio.gather(watching, return_exceptions=True)


# --- the server's routes ------------------------------------------------------------------------


class Ops(World):
    def __init__(self, tmp_path, nifty_fut):
        super().__init__(tmp_path, nifty_fut)
        self.alerts = Alerts()
        self.app = create_app(
            self.platform, self.users, ServerSettings(origin=ORIGIN, metrics_token=KEY),
            broker_login=self, broker_status=lambda a: None, monotonic=self.clock.mono,
            protection=self.protection, alerts=self.alerts,
        )  # fmt: skip


@pytest.fixture
async def ops(tmp_path, nifty_fut):
    w = Ops(tmp_path, nifty_fut)
    yield w
    w.log.close()


async def test_ready_counts_whats_in_the_way_and_the_status_page_says_what(ops):
    c = ops.client()
    dog = asyncio.create_task(ops.platform.supervisor.watchdog())
    await asyncio.sleep(0)
    assert (await c.get("/api/ready")).json() == {"ready": True}
    ops.alerts.raise_("prices", "ERROR", "No prices for over a minute during market hours")
    r = await c.get("/api/ready")
    assert (r.status_code, r.json()) == (503, {"ready": False, "problems": 1})  # no reasons
    admin = await ops.logged_in("ayush")
    page = (await admin.get("/api/status")).json()
    assert (page["state"], page["ready"], page["other_firms_not_ready"]) == ("down", False, 0)
    prices = next(p for p in page["parts"] if p["name"] == "prices")
    assert prices["reason"] == "No prices for over a minute during market hours"
    assert (await (await ops.logged_in("risk")).get("/api/status")).status_code == 200
    r = await (await ops.logged_in("riya")).get("/api/status")
    assert (r.status_code, r.json()["detail"]) == (
        403, "the status page is for admin and risk users")  # fmt: skip
    dog.cancel()
    await asyncio.gather(dog, return_exceptions=True)
    await c.aclose()


async def test_every_answer_names_its_request_and_errors_are_counted(ops):
    from laddrix.metrics import METRICS

    c = ops.client()
    r = await c.get("/api/health", headers={"X-Request-ID": "support-42"})
    assert r.headers["x-request-id"] == "support-42"
    r = await c.get("/api/health", headers={"X-Request-ID": "bad id\r\n"})
    assert len(r.headers["x-request-id"]) == 16
    before = METRICS.snapshot()["counters"].get("http_responses{status=4xx}", 0)
    await c.get("/api/status")  # not signed in
    assert METRICS.snapshot()["counters"]["http_responses{status=4xx}"] == before + 1
    await c.aclose()


async def test_the_monitoring_key_reads_the_prometheus_text_and_nothing_else(ops):
    c = ops.client()
    key = {"Authorization": f"Bearer {KEY}"}
    r = await c.get("/api/metrics", params={"format": "prometheus"}, headers=key)
    assert r.status_code == 200 and "# TYPE laddrix_ready gauge" in r.text
    assert 'laddrix_ops_alerts{level="ERROR"} 0' in r.text
    assert (await c.get("/api/metrics", headers=key)).status_code == 401  # JSON: sign in
    wrong = {"Authorization": f"Bearer {KEY[:-1]}x"}
    r = await c.get("/api/metrics", params={"format": "prometheus"}, headers=wrong)
    assert r.status_code == 401
    admin = await ops.logged_in("ayush")
    assert (await admin.get("/api/metrics", params={"format": "prometheus"})).status_code == 200
    await c.aclose()


async def test_without_a_key_set_no_key_works(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)  # ServerSettings with no metrics_token
    c = w.client()
    r = await c.get("/api/metrics", params={"format": "prometheus"},
                    headers={"Authorization": "Bearer "})  # fmt: skip
    assert r.status_code == 401
    await c.aclose()
    w.log.close()
