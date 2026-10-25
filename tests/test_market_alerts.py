"""Market alerts (K12.3, #233): each kind worked by hand, crossing not touching, armed before
firing, cool-down and re-arming, kept across a restart, and webhooks signed, retried and kept
off private addresses."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from laddrix.alerts.engine import (
    COOLDOWN,
    MAX_ALERTS,
    AlertEngine,
    AlertError,
    alert_to_dict,
    spec,
)
from laddrix.alerts.service import MarketAlerts
from laddrix.alerts.store import AlertStore
from laddrix.alerts.webhook import (
    BACKOFF_S,
    MAX_PENDING,
    WebhookError,
    WebhookSender,
    check_url,
    sign,
)
from laddrix.domain.ticks import Tick

FUT = "NFO:NIFTY26OCTFUT"
NOV = "NFO:NIFTY26NOVFUT"
REL = "NSE:RELIANCE"
T0 = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


class World:
    def __init__(self):
        self.now = T0
        self.latest: dict[str, Tick] = {}
        self.engine = AlertEngine(self.latest.get, lambda i: i in (FUT, NOV, REL),
                                  clock=lambda: self.now)  # fmt: skip
        self.fired = []
        self.engine.on_fire(self.fired.append)

    def tick(self, instrument=FUT, ltp=2500000, **extra):
        t = Tick(instrument, ltp, **extra)
        self.latest[instrument] = t
        self.engine.on_tick(t)
        return t

    def make(self, kind="PRICE_ABOVE", instrument=FUT, user="ayush", **extra):
        params = extra.pop("params", {"level_paise": 2510000})
        return self.engine.create(user, {"kind": kind, "instrument_id": instrument,
                                         "params": params, **extra})  # fmt: skip


@pytest.fixture
def w():
    return World()


def test_crossing_above_fires_once_at_the_level_and_through_a_gap(w):
    w.tick(ltp=2500000)
    a = w.make()
    assert a.armed and a.status == "watching"
    w.tick(ltp=2509990)  # 25,099.90: not yet
    assert w.fired == []
    w.tick(ltp=2510000)  # exactly at the level: crossed
    (f,) = w.fired
    assert f.text == "NIFTY26OCTFUT crossed above 25,100.00 (last 25,100.00)" and f.value == 2510000
    assert a.done and not a.armed and a.fired_count == 1 and a.status == "fired"
    w.tick(ltp=2500000)
    w.tick(ltp=2520000)
    assert len(w.fired) == 1  # "once"
    gap = w.make(params={"level_paise": 2530000})
    w.tick(ltp=2560000)  # straight through the level between two ticks
    assert len(w.fired) == 2 and w.fired[-1].alert is gap


def test_already_true_when_made_waits_for_the_next_crossing(w):
    w.tick(ltp=2520000)
    a = w.make()
    assert not a.armed and a.status == "already true: fires the next time it crosses"
    w.tick(ltp=2525000)
    assert w.fired == []
    w.tick(ltp=2505000)  # back below: armed
    assert a.armed
    w.tick(ltp=2511000)
    assert len(w.fired) == 1


def test_every_time_rearms_once_false_again_and_waits_out_the_cool_down(w):
    w.tick(ltp=2500000)
    a = w.make(kind="PRICE_BELOW", params={"level_paise": 2490000}, repeat="every",
               cooldown_s=60, note="buy the dip")  # fmt: skip
    w.tick(ltp=2489000)
    assert w.fired[-1].text == ("NIFTY26OCTFUT crossed below 24,900.00 (last 24,890.00) · "
                                "buy the dip")  # fmt: skip
    assert not a.done and a.status == "fired: arms again once it is false again"
    w.tick(ltp=2480000)  # still below: not again
    assert len(w.fired) == 1
    w.now += timedelta(seconds=20)
    w.tick(ltp=2495000)  # back above: armed again
    w.tick(ltp=2489500)  # below again, but within the minute
    assert len(w.fired) == 1 and a.status == "cooling down"
    w.now += timedelta(seconds=41)
    w.tick(ltp=2489400)  # still below and the minute has passed
    assert len(w.fired) == 2 and a.fired_count == 2


def test_percent_move_from_the_previous_close(w):
    a = w.make(kind="PCT_MOVE", params={"pct": 2, "direction": "up"})
    assert a.status == "waiting for a price"
    w.tick(ltp=10100)
    assert a.status == "waiting for the previous close"
    w.tick(ltp=10100, close_paise=10000)  # +1%
    assert a.armed
    w.tick(ltp=10199, close_paise=10000)  # +1.99%
    assert w.fired == []
    w.tick(ltp=10200, close_paise=10000)
    assert w.fired[-1].text == "NIFTY26OCTFUT moved +2.00% from the previous close (last 102.00)"
    down = w.make(kind="PCT_MOVE", params={"pct": 1.5, "direction": "down"})
    w.tick(ltp=9900, close_paise=10000)  # −1%: armed
    w.tick(ltp=9850, close_paise=10000)
    assert w.fired[-1].alert is down and w.fired[-1].value == -1.5
    either = w.make(kind="PCT_MOVE", params={"pct": 3})
    assert either.params["direction"] == "either"
    w.tick(ltp=10310, close_paise=10000)
    assert w.fired[-1].alert is either


def test_day_volume_fires_on_reaching_it_and_again_the_next_day(w):
    w.tick(ltp=100, volume=900)
    a = w.make(kind="VOLUME_ABOVE", params={"volume": 1000}, repeat="every", cooldown_s=10)
    w.tick(ltp=100, volume=1000)
    assert w.fired[-1].text == "NIFTY26OCTFUT day volume 1,000 reached 1,000"
    w.tick(ltp=100, volume=5000)
    assert len(w.fired) == 1
    w.now += timedelta(days=1)
    w.tick(ltp=100, volume=200)  # a new day: the count starts again, the alert re-arms
    w.tick(ltp=100, volume=1200)
    assert len(w.fired) == 2 and a.fired_count == 2
    late = w.make(kind="VOLUME_ABOVE", params={"volume": 100})
    assert late.status.startswith("already true")


def test_open_interest_change_from_when_it_armed(w):
    w.tick(ltp=100, open_interest=10_000)
    a = w.make(kind="OI_CHANGE", params={"pct": 5, "direction": "up"})
    assert a.armed and a.baseline_oi == 10_000
    w.tick(ltp=100, open_interest=10_499)
    w.tick(ltp=100, open_interest=9_000)  # down: not this alert's way
    assert w.fired == []
    w.tick(ltp=100, open_interest=10_500)
    assert w.fired[-1].text == "NIFTY26OCTFUT open interest 10,500 is +5.00% from 10,000"
    waiting = w.make(kind="OI_CHANGE", instrument=REL, params={"pct": 1})
    w.tick(REL, 250000)
    assert waiting.status == "waiting for open interest"


def test_a_spread_between_two_contracts_ticks_on_either(w):
    w.tick(FUT, 2500000)
    a = w.make(kind="SPREAD_ABOVE", params={"other": NOV, "level_paise": 15000})
    assert a.status == "waiting for a price of NIFTY26NOVFUT"
    w.tick(NOV, 2490000)  # spread 100.00: armed
    assert a.armed
    w.tick(NOV, 2485000)  # leg B falls: spread 150.00
    assert w.fired[-1].text == "NIFTY26OCTFUT − NIFTY26NOVFUT crossed above 150.00 (now 150.00)"
    w.tick(NOV, 2510000)
    neg = w.make(kind="SPREAD_BELOW", params={"other": NOV, "level_paise": -15000})
    w.tick(FUT, 2494000)  # spread −160.00
    assert w.fired[-1].alert is neg and w.fired[-1].value == -16000


def test_only_the_alerts_on_the_ticking_instrument_are_looked_at(w):
    w.tick(ltp=2500000)
    for _ in range(5):
        w.make()
    w.make(instrument=REL, params={"level_paise": 300000})
    w.engine.checked = 0
    w.tick(REL, 250000)
    assert w.engine.checked == 1
    w.tick(FUT, 2500100)
    assert w.engine.checked == 6
    w.tick(NOV, 1)
    assert w.engine.checked == 6


def test_with_one_price_source_every_alert_is_watched_on_it(w):
    """#440: with no source per user, watched_for() called a missing function."""
    w.make()
    w.make(instrument=REL, params={"level_paise": 300000})
    assert w.engine.watched_for(object()) == w.engine.watched() == {FUT, REL}


def test_what_can_be_asked_is_checked_and_said(w):
    def refused(body, match):
        with pytest.raises(AlertError, match=match):
            w.engine.create("ayush", {"instrument_id": FUT, "params": {"level_paise": 1}, **body})

    refused({"kind": "NOPE"}, "kind must be one of PRICE_ABOVE")
    refused({"kind": "PRICE_ABOVE", "instrument_id": "NSE:NOPE"}, "unknown instrument NSE:NOPE")
    refused({"kind": "PRICE_ABOVE", "params": {}}, "Price is required")
    refused({"kind": "PRICE_ABOVE", "params": {"level_paise": 1, "x": 2}}, "no setting called 'x'")
    refused({"kind": "PCT_MOVE", "params": {"pct": 0}}, "Move must be at least 0.01")
    refused({"kind": "PCT_MOVE", "params": {"pct": 1, "direction": "sideways"}}, "must be one of")
    refused({"kind": "SPREAD_ABOVE", "params": {"other": FUT, "level_paise": 1}},
            "Leg B must be another contract")  # fmt: skip
    refused({"kind": "SPREAD_ABOVE", "params": {"other": "NFO:X", "level_paise": 1}},
            "unknown instrument NFO:X")  # fmt: skip
    refused({"kind": "PRICE_ABOVE", "repeat": "always"}, "repeat must be once or every")
    refused({"kind": "PRICE_ABOVE", "cooldown_s": 1}, f"cool-down must be {COOLDOWN['min']}")
    refused({"kind": "PRICE_ABOVE", "note": "x" * 201}, "at most 200 characters")
    refused({"kind": "PRICE_ABOVE", "webhook": "yes"}, "webhook and enabled are yes or no")
    with pytest.raises(AlertError, match="params must be an object"):
        w.engine.create("ayush", {"kind": "PRICE_ABOVE", "instrument_id": FUT, "params": [1]})
    for _ in range(MAX_ALERTS):
        w.make(user="riya")
    with pytest.raises(AlertError, match=f"at most {MAX_ALERTS} alerts each"):
        w.make(user="riya")
    s = spec()
    assert [k["code"] for k in s["kinds"]][:3] == ["PRICE_ABOVE", "PRICE_BELOW", "PCT_MOVE"]
    assert s["kinds"][0]["params"][0]["kind"] == "price" and s["repeat"] == ["once", "every"]


def test_changing_switching_on_again_and_deleting(w):
    w.tick(ltp=2500000)
    a = w.make()
    with pytest.raises(LookupError):
        w.engine.get("riya", a.alert_id)  # another user's alert isn't found
    with pytest.raises(AlertError):
        w.engine.update("ayush", a.alert_id, {"params": {"level_paise": -5}})
    assert a.params == {"level_paise": 2510000}  # a refused change changes nothing
    w.engine.update(
        "ayush", a.alert_id, {"kind": "PRICE_BELOW", "params": {"level_paise": 2490000}}
    )
    w.tick(ltp=2489000)
    assert a.done and len(w.fired) == 1
    w.engine.update("ayush", a.alert_id, {"enabled": True})  # watched again
    assert not a.done and a.status.startswith("already true")
    w.engine.update("ayush", a.alert_id, {"enabled": False})
    assert a.status == "off"
    w.tick(ltp=2500000)
    w.tick(ltp=2480000)
    assert len(w.fired) == 1
    w.engine.update("ayush", a.alert_id, {"instrument_id": REL, "params": {"level_paise": 100},
                                          "enabled": True})  # fmt: skip
    assert w.engine.watched() == {REL}
    w.engine.delete("ayush", a.alert_id)
    assert w.engine.watched() == set()
    w.engine.checked = 0
    w.tick(REL, 50)
    w.tick(FUT, 1)
    assert w.engine.checked == 0 and w.engine.of("ayush") == []


# --- kept across a restart ----------------------------------------------------------------


async def test_alerts_and_the_webhook_are_kept_across_a_restart(w, tmp_path):
    store = AlertStore(tmp_path)
    service = MarketAlerts(w.engine, store)
    w.tick(ltp=2500000)
    body = {"kind": "PRICE_ABOVE", "instrument_id": FUT, "params": {"level_paise": 2510000}}
    made = service.create("ayush", {**body, "note": "breakout"})
    hook = service.set_webhook("ayush", "https://hooks.example.com/abc")
    assert hook["secret"] and service.webhook("ayush") == {"url": "https://hooks.example.com/abc",
                                                            "set": True}  # fmt: skip
    await store.flush()
    (path,) = tmp_path.glob("*.json")
    data = json.loads(path.read_text())
    assert data["user"] == "ayush"
    assert data["alerts"][0]["note"] == "breakout" and data["webhook"]["secret"] == hook["secret"]
    again = World()
    second = MarketAlerts(again.engine, AlertStore(tmp_path))
    assert second.start() == 1
    (kept,) = second.list("ayush")
    assert kept["alert_id"] == made["alert_id"] and kept["status"] == "waiting for a price"
    again.tick(ltp=2500000)
    again.tick(ltp=2511000)
    assert len(again.fired) == 1
    assert (
        second.webhook("ayush")["set"] and second.store.webhook("ayush")["secret"] == hook["secret"]
    )
    (tmp_path / "broken.json").write_text("{not json")
    assert len(AlertStore(tmp_path).load_all()) == 1  # a broken file is skipped, said in the log
    odd = AlertStore(tmp_path)
    odd.set_webhook("../etc/a b@x", None)  # any user id is a safe file name
    assert all(p.parent == tmp_path for p in tmp_path.iterdir())
    named = [p for p in tmp_path.glob("*.json") if p.name != "broken.json"]
    users = {json.loads(p.read_text())["user"] for p in named}
    assert users == {"ayush", "../etc/a b@x"}


# --- webhooks -----------------------------------------------------------------------------


def test_webhook_addresses_must_be_public_https():
    assert check_url(" https://hooks.example.com/x ") == "https://hooks.example.com/x"
    cases = [
        ("http://hooks.example.com", "https://"),
        ("https://10.0.0.5/x", "public"),
        ("https://127.0.0.1/x", "public"),
        ("https://[::1]/x", "public"),
        ("https://169.254.169.254/latest", "public"),  # the cloud metadata address
        ("https://localhost/x", "public"),
        ("https://user:pw@hooks.example.com", "user name"),
        ("ftp://x", "https://"),
        ("https://", "https://"),
        ("https://a.example.com/" + "x" * 500, "too long"),
    ]
    for bad, why in cases:
        with pytest.raises(WebhookError, match=why):
            check_url(bad)


def test_the_signature_is_hmac_sha256_over_timestamp_and_body():
    body = b'{"a":1}'
    expected = hmac.new(b"s3cret", b"1760598000." + body, hashlib.sha256).hexdigest()
    assert sign("s3cret", 1760598000, body) == f"sha256={expected}"


def sender_with(handler, resolved=("93.184.216.34",)):
    waits = []

    async def sleep(s):
        waits.append(s)

    async def resolver(host):
        return list(resolved)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookSender(
        client=client, resolver=resolver, sleep=sleep, now=lambda: 1760598000
    ), waits


async def test_a_webhook_is_signed_and_retried_on_server_errors():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503 if len(seen) < 3 else 200)

    sender, waits = sender_with(handler)
    result = await sender.deliver_now("https://hooks.example.com/x", "s3cret", {"text": "hi"})
    assert result == "" and len(seen) == 3 and waits == [BACKOFF_S[0], BACKOFF_S[1]]
    r = seen[0]
    assert r.headers["x-laddrix-timestamp"] == "1760598000"
    assert r.headers["x-laddrix-signature"] == sign("s3cret", 1760598000, r.content)
    assert (
        json.loads(r.content) == {"text": "hi"} and r.headers["content-type"] == "application/json"
    )
    await sender.aclose()


async def test_a_refusal_is_not_retried_and_a_dead_host_gives_up():
    calls = []

    def refuse(request):
        calls.append(1)
        return httpx.Response(401)

    sender, waits = sender_with(refuse)
    assert (
        await sender.deliver_now("https://hooks.example.com/x", "s", {}) == "refused: answered 401"
    )
    assert len(calls) == 1 and waits == []

    def down(request):
        raise httpx.ConnectError("no route")

    sender, waits = sender_with(down)
    assert (
        await sender.deliver_now("https://hooks.example.com/x", "s", {})
        == "not reached: ConnectError"
    )
    assert waits == list(BACKOFF_S) and sender.failed == 1


async def test_a_host_that_resolves_to_a_private_address_is_refused():
    def never(request):
        raise AssertionError("must not be called")

    sender, _ = sender_with(never, resolved=("10.1.2.3",))
    assert await sender.deliver_now("https://evil.example.com/x", "s", {}) == (
        "the webhook host is not a public address"
    )
    sender, _ = sender_with(never, resolved=())
    assert "can't be found" in await sender.deliver_now("https://gone.example.com/x", "s", {})


async def test_sends_never_pile_up_without_end():
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return httpx.Response(200)

    sender, _ = sender_with(slow)
    results = []
    for _ in range(MAX_PENDING + 2):
        sender.send("https://hooks.example.com/x", "s", {}, results.append)
    assert results == ["too many webhook sends waiting: this one was dropped"] * 2
    gate.set()
    await sender.aclose()
    assert results.count("") == MAX_PENDING


async def test_a_fired_alert_reaches_the_webhook_and_its_result_the_alert(w, tmp_path):
    got = []

    def handler(request):
        got.append(json.loads(request.content))
        return httpx.Response(200)

    sender, _ = sender_with(handler)
    audit = []
    service = MarketAlerts(
        w.engine,
        AlertStore(tmp_path),
        sender,
        audit=lambda k, detail, extra: audit.append((k, detail)),
    )
    fired = []
    service.on_fire(fired.append)
    service.set_webhook("ayush", "https://hooks.example.com/x")
    w.tick(ltp=2500000)
    a = service.create("ayush", {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                 "params": {"level_paise": 2510000}, "webhook": True})  # fmt: skip
    quiet = service.create("ayush", {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                     "params": {"level_paise": 2510000}})  # fmt: skip
    w.tick(ltp=2512000)
    await sender.aclose()
    assert len(fired) == 2 and len(got) == 1  # only the alert that asked for it
    assert got[0]["type"] == "market_alert" and got[0]["alert"]["alert_id"] == a["alert_id"]
    assert got[0]["text"] == "NIFTY26OCTFUT crossed above 25,100.00 (last 25,120.00)"
    kept = w.engine.get("ayush", a["alert_id"])
    assert kept.webhook_error == "" and kept.webhook_ok_at is not None
    assert [k for k, _ in audit] == ["ALERT_WEBHOOK_SET", "ALERT_CREATED", "ALERT_CREATED",
                                     "ALERT_FIRED", "ALERT_FIRED"]  # fmt: skip
    assert alert_to_dict(w.engine.get("ayush", quiet["alert_id"]))["active"] is False
    service.clear_webhook("ayush")
    b = service.create("ayush", {"kind": "PRICE_BELOW", "instrument_id": FUT,
                                 "params": {"level_paise": 2505000}, "webhook": True})  # fmt: skip
    w.tick(ltp=2500000)
    assert w.engine.get("ayush", b["alert_id"]).webhook_error == "no webhook is set"
    with pytest.raises(AlertError, match="no webhook is set"):
        await service.test_webhook("ayush")
    with pytest.raises(AlertError, match="https://"):
        service.set_webhook("ayush", "http://plain.example.com")


# --- the API and the stream ---------------------------------------------------------------


@pytest.fixture
async def live(tmp_path, nifty_fut):
    """A running server with alerts; webhooks go to a fake receiver."""
    from test_stream import Live

    from laddrix.server.app import ServerSettings, create_app

    server = Live(tmp_path, nifty_fut)
    server.received = []

    def receiver(request):
        server.received.append(request)
        return httpx.Response(200)

    sender, _ = sender_with(receiver)
    engine = AlertEngine(server.md.latest, lambda i: server.store.get(i) is not None)
    server.alerts_service = MarketAlerts(engine, AlertStore(tmp_path / "alerts"), sender)
    server.md.tap(engine.on_tick)
    server.app = create_app(server.platform, server.users,
                            ServerSettings(origin=server.origin, cookie_secure=False),
                            market_alerts=server.alerts_service)  # fmt: skip
    server.hub = server.app.state.hub
    await server.start()
    yield server
    await server.stop()
    await sender.aclose()


async def test_the_alerts_api_is_each_users_own(live):
    a = await live.session("ayush")
    r = await live.session("riya")
    spec_ = (await a.get("/api/alerts/spec")).json()
    assert spec_["max_alerts"] == MAX_ALERTS and spec_["kinds"][0]["code"] == "PRICE_ABOVE"
    body = {"kind": "PRICE_ABOVE", "instrument_id": FUT, "params": {"level_paise": 2510000}}
    made = await a.post("/api/alerts", json=body)
    assert made.status_code == 201, made.text
    alert_id = made.json()["alert_id"]
    assert made.json()["status"] == "watching"  # the price is 25,010.00
    assert [x["alert_id"] for x in (await a.get("/api/alerts")).json()] == [alert_id]
    assert (await r.get("/api/alerts")).json() == []  # riya sees none of ayush's
    assert (await r.patch(f"/api/alerts/{alert_id}", json={"enabled": False})).status_code == 404
    assert (await r.delete(f"/api/alerts/{alert_id}")).status_code == 404
    changed = await a.patch(f"/api/alerts/{alert_id}", json={"params": {"level_paise": 2520000}})
    assert changed.json()["params"] == {"level_paise": 2520000}
    bad = await a.patch(f"/api/alerts/{alert_id}", json={"repeat": "always"})
    assert bad.status_code == 422 and "repeat must be once or every" in bad.json()["detail"]
    nope = await a.post("/api/alerts", json={**body, "kind": "PRICE_SIDEWAYS"})
    assert nope.status_code == 422 and "kind must be one of" in nope.json()["detail"]
    assert (await a.post("/api/alerts", json={**body, "colour": "red"})).status_code == 422
    assert (await a.delete(f"/api/alerts/{alert_id}")).status_code == 204
    assert (await a.get("/api/alerts")).json() == []
    for c in (a, r):
        await c.aclose()


async def test_the_webhook_api_shows_the_secret_once_and_tests_it(live):
    a = await live.session("ayush")
    assert (await a.get("/api/alerts/webhook")).json() == {"url": None, "set": False}
    assert (await a.post("/api/alerts/webhook/test", json={})).status_code == 422  # none set
    plain = await a.put("/api/alerts/webhook", json={"url": "http://hooks.example.com/x"})
    assert plain.status_code == 422 and "https://" in plain.json()["detail"]
    inside = await a.put("/api/alerts/webhook", json={"url": "https://192.168.1.5/x"})
    assert inside.status_code == 422 and "public" in inside.json()["detail"]
    s = await a.put("/api/alerts/webhook", json={"url": "https://hooks.example.com/x"})
    secret = s.json()["secret"]
    assert len(secret) >= 40
    assert (await a.get("/api/alerts/webhook")).json() == {
        "url": "https://hooks.example.com/x",
        "set": True,
    }  # never the secret again
    test = (await a.post("/api/alerts/webhook/test", json={})).json()
    assert test == {"ok": True, "error": ""}
    (req,) = live.received
    assert req.headers["x-laddrix-signature"] == sign(secret, 1760598000, req.content)
    assert json.loads(req.content)["type"] == "test"
    assert (await a.delete("/api/alerts/webhook")).status_code == 204
    assert (await a.get("/api/alerts/webhook")).json()["set"] is False
    await a.aclose()


async def test_a_fired_alert_reaches_its_users_screens_only(live):
    from test_stream import all_of, next_of

    a = await live.session("ayush")
    r = await live.session("riya")
    body = {"kind": "PRICE_ABOVE", "instrument_id": FUT, "params": {"level_paise": 2502000}}
    await a.post("/api/alerts", json={**body, "note": "go"})
    async with live.stream(a) as ws_a, live.stream(r) as ws_r:
        await next_of(ws_a, "hello")
        await next_of(ws_r, "hello")
        live.md.on_tick(Tick(FUT, 2503000))
        got = await next_of(ws_a, "market_alert")
        assert got["text"] == "NIFTY26OCTFUT crossed above 25,020.00 (last 25,030.00) · go"
        assert got["alert"]["fired_count"] == 1 and got["alert"]["active"] is False
        state = await next_of(ws_a, "market_alert_state")
        assert state["alert"]["status"] == "fired"
        assert await all_of(ws_r, "market_alert", quiet=0.4) == []
    for c in (a, r):
        await c.aclose()


async def test_the_server_keeps_alert_instruments_watched_so_their_prices_come(tmp_path, nifty_fut):
    from laddrix.alerts.service import build_market_alerts
    from laddrix.marketdata.service import MarketData
    from laddrix.platform import Platform
    from laddrix.refdata.store import InstrumentStore
    from laddrix.risk.kill_switch import KillSwitch

    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    platform = Platform(store, md, KillSwitch("firm"))
    service = build_market_alerts(platform, tmp_path)
    assert md.watched() == set()
    made = service.create("ayush", {"kind": "PRICE_ABOVE", "instrument_id": FUT,
                                    "params": {"level_paise": 2510000}})  # fmt: skip
    assert md.watched() == {FUT}  # a feed (Kite) now streams it, though no screen shows it
    md.on_tick(Tick(FUT, 2500000))
    md.on_tick(Tick(FUT, 2511000))
    assert service.list("ayush")[0]["fired_count"] == 1
    service.delete("ayush", made["alert_id"])
    assert md.watched() == set()
