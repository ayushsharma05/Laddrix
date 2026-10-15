"""GTT: the broker holds the order until a price is touched (#201)."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from mocktrade.broker.base import BrokerError, GttBook
from mocktrade.broker.fake import FakeBroker
from mocktrade.broker.kite.broker import KiteBroker
from mocktrade.broker.kite.gtt import gtt_params, parse_gtt
from mocktrade.broker.kite.http import KiteHttp
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.gtt import (
    GttKind,
    GttLeg,
    GttRequest,
    GttStatus,
    gtt_problems,
    standard_gtt_spec,
)
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    Product,
    Side,
)
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.gtt import GttError
from mocktrade.orders.model import ClientIds, EventKind
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch
from mocktrade.risk.limits import RiskLimits
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"
LTP = 2501000
CALL = Instrument(Exchange.NFO, "NIFTY26OCT25500CE", InstrumentKind.CALL, 5, 75,
                  date(2026, 10, 27), 2550000, "NIFTY")  # fmt: skip


def tick(ltp, instrument=FUT):
    return Tick(instrument, ltp, bids=(DepthLevel(ltp - 10, 1500, 2),),
                asks=(DepthLevel(ltp + 10, 1500, 1),))  # fmt: skip


def single(trigger=2480000, price=2478000, side=Side.SELL, qty=75, account="acc"):
    leg = GttLeg(trigger, side, qty, price)
    return GttRequest(account, "ayush", FUT, Product.NRML, GttKind.SINGLE, (leg,))


def oco(low=2480000, high=2530000, side=Side.SELL, qty=75):
    legs = (GttLeg(low, side, qty, low - 2000), GttLeg(high, side, qty, high - 1000))
    return GttRequest("acc", "ayush", FUT, Product.NRML, GttKind.OCO, legs)


# --- the rules ---


def test_the_rules_are_checked_with_every_problem_named(nifty_fut):
    spec = standard_gtt_spec(nifty_fut, (Product.NRML, Product.MIS))
    assert gtt_problems(spec, single(), LTP) == []
    assert gtt_problems(spec, oco(), LTP) == []
    near = gtt_problems(spec, single(trigger=2500000, price=2499000), LTP)
    assert near == ["the GTT: trigger 25,000.00 is only 0.04% from the last price 25,010.00 "
                    "(at least 0.25%)"]  # fmt: skip
    bad = gtt_problems(spec, single(trigger=2480005, price=2478000, qty=70), LTP)
    assert "the GTT: quantity must be whole lots of 75" in bad
    assert "the GTT: trigger must be on the 0.10 grid" in bad
    assert "an OCO's first leg must have the lower trigger" in gtt_problems(
        spec, oco(low=2530000, high=2480000), LTP
    )
    assert "an OCO needs one trigger below the last price and one above" in gtt_problems(
        spec, oco(low=2470000, high=2480000), LTP
    )
    mixed = GttRequest("acc", "ayush", FUT, Product.NRML, GttKind.OCO,
                       (GttLeg(2480000, Side.SELL, 75, 2478000),
                        GttLeg(2530000, Side.BUY, 75, 2531000)))  # fmt: skip
    assert "both legs of an OCO close the same position: same side" in gtt_problems(
        spec, mixed, LTP
    )
    two = GttRequest("acc", "ayush", FUT, Product.NRML, GttKind.SINGLE, oco().legs)
    assert gtt_problems(spec, two, LTP) == ["a single GTT has 1 leg"]
    cnc = GttRequest("acc", "ayush", FUT, Product.CNC, GttKind.SINGLE, single().legs)
    assert "CNC is not available on NFO:NIFTY26OCTFUT" in gtt_problems(spec, cnc, LTP)


# --- the paper broker holds GTTs as Kite does ---


@pytest.fixture
async def paper(nifty_fut):
    store, md = InstrumentStore(), MarketData()
    store.add(nifty_fut)
    md.on_tick(tick(LTP))
    broker = PaperBroker(md, store, latency_s=0.0)
    yield broker, md
    await broker.aclose()


async def test_a_paper_gtt_fires_only_when_its_trigger_is_touched(paper):
    broker, md = paper
    assert isinstance(broker, GttBook)
    gtt_id = await broker.place_gtt(single(), LTP)
    broker.on_tick(tick(2485000))
    assert (await broker.gtts())[0].status is GttStatus.ACTIVE
    broker.on_tick(tick(2480000))  # touched
    await broker.settle()
    (view,) = await broker.gtts()
    assert (view.gtt_id, view.status, view.fired_leg) == (gtt_id, GttStatus.TRIGGERED, 0)
    (order,) = await broker.orders()
    assert order.broker_order_id == view.order_id and order.client_order_id == "GTTG1"
    broker.on_tick(tick(2470000))
    assert len(await broker.orders()) == 1  # it fires once


async def test_a_paper_oco_fires_one_leg_only(paper):
    broker, md = paper
    await broker.place_gtt(oco(), LTP)
    broker.on_tick(tick(2531000))  # the target side
    await broker.settle()
    (view,) = await broker.gtts()
    assert (view.status, view.fired_leg) == (GttStatus.TRIGGERED, 1)
    broker.on_tick(tick(2470000))
    assert len(await broker.orders()) == 1


async def test_a_paper_gtt_refuses_bad_requests_and_changes_to_finished_ones(paper):
    broker, md = paper
    with pytest.raises(BrokerError, match="only 0.04%"):
        await broker.place_gtt(single(trigger=2500000, price=2499000), LTP)
    gtt_id = await broker.place_gtt(single(), LTP)
    await broker.delete_gtt(gtt_id)
    assert (await broker.gtts())[0].status is GttStatus.DELETED
    with pytest.raises(BrokerError, match="is deleted"):
        await broker.modify_gtt(gtt_id, single(trigger=2470000, price=2468000), LTP)


# --- the service: checks, audit, adoption ---


class World:
    def __init__(self, tmp_path, nifty_fut, broker=None, **limits):
        self.store, self.md = InstrumentStore(), MarketData()
        self.store.add(nifty_fut)
        self.store.add(CALL)
        self.md.on_tick(tick(LTP))
        self.md.on_tick(tick(12000, CALL.id))
        self.log = EventLog(tmp_path / "events.db")
        self.log.start()
        self.platform = Platform(self.store, self.md, KillSwitch("firm"), self.log)
        self.broker = broker or PaperBroker(self.md, self.store, latency_s=0.0)
        if isinstance(self.broker, PaperBroker):
            self.broker.start()
        self.rt = self.platform.add_account(
            BrokerAccount("acc", "paper", "P1", "ayush"), self.broker, ids=ClientIds("MT"),
            limits=RiskLimits(max_order_value_paise=10**12, **limits),
        )  # fmt: skip
        self.rt.gtt._poll_s = 0.02
        self.seen = []
        self.rt.gtt.subscribe(self.seen.append)

    async def close(self):
        if isinstance(self.broker, PaperBroker):
            await self.broker.aclose()
        await self.log.aclose()

    def notes(self):
        return [e.kind for e in self.log.events(account_id="acc") if e.kind.startswith("GTT")]


async def test_a_fired_gtt_order_is_adopted_and_closes_the_position(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    run = asyncio.create_task(w.platform.run(reconcile=False))
    try:
        buy = await w.rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.BUY, 75,
                                                    OrderType.LIMIT, Product.NRML,
                                                    price_paise=LTP + 10))  # fmt: skip
        async with asyncio.timeout(3):
            while w.rt.positions.net_qty(FUT) != 75:
                await asyncio.sleep(0.01)
        view = await w.rt.gtt.place(single())
        assert view.status is GttStatus.ACTIVE and w.seen[-1] is view
        w.md.on_tick(tick(2480000))  # touched: the broker places a sell at 24,780.00 limit,
        # which the 24,799.90 bid fills at once
        async with asyncio.timeout(3):
            while w.rt.positions.net_qty(FUT) != 0:
                await asyncio.sleep(0.01)
        fired = w.rt.gtt.get(view.gtt_id)
        assert fired.status is GttStatus.TRIGGERED and fired.order_id
        adopted = next(o for o in w.rt.orders.orders() if o.broker_order_id == fired.order_id)
        assert adopted.client_order_id.startswith("MT") and adopted.status is OrderStatus.FILLED
        assert adopted.client_order_id != buy.client_order_id
        await w.log.flush()
        assert w.notes() == ["GTT_PLACED", "GTT_TRIGGERED"]
        kinds = [e.kind for e in w.log.events(account_id="acc")]
        assert EventKind.EXTERNAL.value not in kinds  # never mistaken for someone else's
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        await w.close()


async def test_place_refuses_in_words_and_judges_risk_at_the_trigger(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    with pytest.raises(GttError, match="only 0.04%"):
        await w.rt.gtt.place(single(trigger=2500000, price=2499000))
    far = single(trigger=2300000, price=2299000)  # 8% away: fine for a stop at its trigger
    assert (await w.rt.gtt.place(far)).status is GttStatus.ACTIVE
    naked = GttRequest("acc", "ayush", CALL.id, Product.NRML, GttKind.SINGLE,
                       (GttLeg(15000, Side.SELL, 75, 14900),))  # fmt: skip
    with pytest.raises(GttError, match="selling options to open a short"):
        await w.rt.gtt.place(naked)
    with pytest.raises(GttError, match="is for other"):
        await w.rt.gtt.place(single(account="other"))
    await w.close()


async def test_modify_and_delete_are_audited_and_finished_ones_are_refused(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    view = await w.rt.gtt.place(single())
    changed = await w.rt.gtt.modify(view.gtt_id, single(trigger=2470000, price=2468000))
    assert changed.request.legs[0].trigger_paise == 2470000
    gone = await w.rt.gtt.delete(view.gtt_id, "ayush")
    assert gone.status is GttStatus.DELETED
    with pytest.raises(GttError, match="is deleted: it can't be changed"):
        await w.rt.gtt.delete(view.gtt_id, "ayush")
    await w.log.flush()
    assert w.notes() == ["GTT_PLACED", "GTT_MODIFIED", "GTT_DELETED"]
    await w.close()


class Flaky(FakeBroker):
    """A GTT broker whose place call gets no clear answer."""

    def gtt_spec(self, instrument):
        return standard_gtt_spec(instrument, (Product.NRML,))

    async def place_gtt(self, request, ltp_paise):
        raise httpx.ReadTimeout("slow")

    async def modify_gtt(self, gtt_id, request, ltp_paise):
        raise BrokerError("Trigger not found")

    async def delete_gtt(self, gtt_id):
        raise BrokerError("x")

    async def gtts(self):
        return []


async def test_no_clear_answer_says_check_the_list_never_retries(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut, broker=Flaky())
    with pytest.raises(GttError, match="check the GTT list before placing it again"):
        await w.rt.gtt.place(single())
    assert w.rt.gtt.list() == []
    await w.close()


async def test_a_broker_without_gtts_has_no_gtt_service(tmp_path, nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    platform = Platform(store, MarketData(), KillSwitch("firm"))
    rt = platform.add_account(BrokerAccount("acc", "paper", "P1", "ayush"), FakeBroker(),
                              ids=ClientIds("MT"))  # fmt: skip
    assert rt.gtt is None


# --- Kite ---


def test_kite_gtt_params_and_reading_a_fired_trigger_back():
    params = gtt_params(oco(), LTP)
    assert params["type"] == "two-leg"
    assert json.loads(params["condition"]) == {
        "exchange": "NFO", "tradingsymbol": "NIFTY26OCTFUT",
        "trigger_values": [24800.0, 25300.0], "last_price": 25010.0,
    }  # fmt: skip
    orders = json.loads(params["orders"])
    assert orders[1] == {"exchange": "NFO", "tradingsymbol": "NIFTY26OCTFUT",
                         "transaction_type": "SELL", "quantity": 75, "order_type": "LIMIT",
                         "product": "NRML", "price": 25290.0}  # fmt: skip
    row = {
        "id": 123, "type": "two-leg", "status": "triggered",
        "created_at": "2026-10-15 10:00:00", "updated_at": "2026-10-15 11:00:00",
        "condition": json.loads(params["condition"]),
        "orders": [dict(orders[0], result=None),
                   dict(orders[1], result={"order_result": {"order_id": "2310150001",
                                                            "status": "success"}})],
    }  # fmt: skip
    view = parse_gtt(row, "acc")
    assert (view.gtt_id, view.status, view.order_id, view.fired_leg) == (
        "123",
        GttStatus.TRIGGERED,
        "2310150001",
        1,
    )
    assert view.request.legs == oco().legs and view.created_at.hour == 10
    assert parse_gtt({"id": 1, "type": "weird"}, "acc") is None


async def test_kite_gtt_calls():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"status": "success", "data": {"trigger_id": 77}})
        if request.method == "GET":
            return httpx.Response(200, json={"status": "success", "data": []})
        return httpx.Response(200, json={"status": "success", "data": {"trigger_id": 77}})

    http = KiteHttp("key", "tok", transport=httpx.MockTransport(handler))
    broker = KiteBroker(http, kite_user_id="AB1234", account_id="acc")
    assert await broker.place_gtt(single(), LTP) == "77"
    await broker.modify_gtt("77", single(trigger=2470000, price=2468000), LTP)
    await broker.delete_gtt("77")
    assert await broker.gtts() == []
    assert seen == [("POST", "/gtt/triggers"), ("PUT", "/gtt/triggers/77"),
                    ("DELETE", "/gtt/triggers/77"), ("GET", "/gtt/triggers")]  # fmt: skip
    await http.aclose()


# --- the API ---


class Holding(FakeBroker):
    """A fake broker that holds GTTs in a dict."""

    def __init__(self):
        super().__init__()
        self.held = {}

    def gtt_spec(self, instrument):
        return standard_gtt_spec(instrument, (Product.NRML, Product.MIS))

    async def place_gtt(self, request, ltp_paise):
        gtt_id = str(len(self.held) + 1)
        self.held[gtt_id] = request
        return gtt_id

    async def modify_gtt(self, gtt_id, request, ltp_paise):
        self.held[gtt_id] = request

    async def delete_gtt(self, gtt_id):
        self.held.pop(gtt_id)

    async def gtts(self):
        return []


async def test_the_gtt_api(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    from mocktrade.orders.gtt import GttService

    w = ServerWorld(tmp_path, nifty_fut)
    rt = w.platform.account("acc-ayush")
    broker = Holding()
    rt.gtt = GttService("acc-ayush", broker, rt.orders, rt.risk, w.platform.instruments.get,
                        w.platform.market_data.latest)  # fmt: skip
    c = await w.logged_in()
    spec = (await c.get(f"/api/accounts/acc-ayush/gtt-spec?instrument_id={FUT}")).json()
    assert spec["kinds"] == ["single", "two-leg"] and spec["min_distance_pct"] == 0.25
    assert "works even if MockTrade is down" in spec["help"]["single"]
    body = {"instrument_id": FUT, "product": "NRML", "kind": "single",
            "legs": [{"trigger_paise": 2480000, "side": "SELL", "qty": 75,
                      "price_paise": 2478000}]}  # fmt: skip
    r = await c.post("/api/accounts/acc-ayush/gtt", json=body)
    assert r.status_code == 201, r.text
    placed = r.json()
    assert placed["status"] == "active" and placed["legs"][0]["trigger_paise"] == 2480000
    near = dict(body, legs=[dict(body["legs"][0], trigger_paise=2500000)])
    r = await c.post("/api/accounts/acc-ayush/gtt", json=near)
    assert r.status_code == 422 and "only 0.04%" in r.json()["detail"]
    assert [g["gtt_id"] for g in (await c.get("/api/accounts/acc-ayush/gtt")).json()] == ["1"]
    r = await c.delete("/api/accounts/acc-ayush/gtt/1")
    assert r.json()["status"] == "deleted" and broker.held == {}
    other = w.platform.account("acc-ayush").gtt
    w.platform.account("acc-ayush").gtt = None
    r = await c.get("/api/accounts/acc-ayush/gtt")
    assert r.status_code == 404 and "doesn't hold GTT orders" in r.json()["detail"]
    w.platform.account("acc-ayush").gtt = other
    await c.aclose()
    w.log.close()


async def test_an_oco_fires_at_exactly_its_lower_trigger(paper):
    broker, md = paper
    await broker.place_gtt(oco(), LTP)
    broker.on_tick(tick(2480000))  # exactly the stop
    await broker.settle()
    (view,) = await broker.gtts()
    assert (view.status, view.fired_leg) == (GttStatus.TRIGGERED, 0)
