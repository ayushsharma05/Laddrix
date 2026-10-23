"""Surveillance alerts for risk (#291): each kind worked by hand.

The gap before: nothing watched for the patterns an exchange or a regulator asks about.
Self-trades, unusual sizes, heavy cancelling and bursts of orders were only visible by
reading the audit trail line by line."""

from datetime import UTC, datetime, timedelta

import pytest

from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.orders.model import EventKind, OrderEvent, OrderView, Pending
from mocktrade.risk.surveillance import BURST, CHURN, LARGE, SELF_TRADE, Surveillance

FUT = "NFO:NIFTY26OCTFUT"
T0 = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)  # 09:30 India time


class World:
    def __init__(self):
        self.now = T0
        self.notes = []
        self.s = Surveillance(note=lambda k, a, d, x: self.notes.append((k, a, d)),
                              clock=lambda: self.now)  # fmt: skip
        self.n = 0

    def order(self, side=Side.BUY, qty=75, price=2501000, account="acc", user="ayush",
              kind=EventKind.SUBMITTED, cid=None, status=OrderStatus.OPEN, at=None):  # fmt: skip
        if cid is None:
            self.n += 1
            cid = f"MT{self.n}"
        market = price is None
        r = OrderRequest(cid, account, user, FUT, side, qty,
                         OrderType.MARKET if market else OrderType.LIMIT, Product.NRML,
                         price_paise=None if market else price)  # fmt: skip
        if at is None:
            self.now += timedelta(seconds=1)  # one order a second: no burst unless asked
        ts = at or self.now
        view = OrderView(r, "B" + cid, status, 0, 0, "", Pending.NONE, ts, ts)
        self.s.on_order_event(OrderEvent(kind, ts, account, cid, view))
        return cid

    def kinds(self):
        return [a.kind for a in self.s.alerts()]


@pytest.fixture
def w():
    return World()


def test_a_possible_self_trade_same_account_or_same_user(w):
    w.order(Side.BUY, price=2501000)
    w.order(Side.SELL, price=2502000)  # above the buy: they can't match
    assert w.kinds() == []
    w.order(Side.SELL, price=2500000)  # at or below the buy: they could
    (a,) = w.s.alerts()
    assert a.kind == SELF_TRADE and "could match BUY order MT1 of the same account" in a.words
    w.order(Side.SELL, price=None, account="acc-2", user="riya")  # someone else: fine
    w.order(Side.BUY, price=None, account="acc-3", user="riya")  # riya against riya: alert
    assert w.kinds() == [SELF_TRADE, SELF_TRADE]
    assert "of the same user" in w.s.alerts()[1].words


def test_a_finished_order_cant_be_matched_any_more(w):
    buy = w.order(Side.BUY, price=2501000)
    w.order(Side.BUY, cid=buy, kind=EventKind.UPDATED, status=OrderStatus.CANCELLED)
    w.order(Side.SELL, price=2500000)
    assert w.kinds() == []


def test_an_unusually_large_order(w):
    for _ in range(10):
        w.order(qty=75)
    w.order(qty=300)  # 4x: big, not unusual
    assert w.kinds() == []
    w.order(qty=750)  # 10x the median
    (a,) = w.s.alerts()
    assert a.kind == LARGE and "10.0x this account's median of 75 today" in a.words


def test_large_needs_a_day_of_orders_first(w):
    w.order(qty=75)
    w.order(qty=7500)
    assert w.kinds() == []


def test_a_high_cancel_and_modify_ratio(w):
    ids = [w.order(price=2400000 + i * 100) for i in range(10)]  # resting, no crossing
    for i in range(29):
        w.order(cid=ids[i % 10], kind=EventKind.MODIFY_SENT)
    assert w.kinds() == []
    w.order(cid=ids[0], kind=EventKind.CANCEL_SENT)  # 30 for 10 orders: 3x
    (a,) = w.s.alerts()
    assert a.kind == CHURN and "30 cancels and changes for 10 orders today (3.0x)" in a.words


def test_a_burst_near_the_exchanges_ten_a_second(w):
    for i in range(7):
        w.order(side=Side.BUY, price=2400000, at=T0 + timedelta(milliseconds=100 * i))
    assert w.kinds() == []
    w.order(side=Side.BUY, price=2400000, at=T0 + timedelta(milliseconds=700))
    assert w.kinds() == [BURST]
    for i in range(8):  # again within the minute: not said twice
        w.order(side=Side.BUY, price=2400000, at=T0 + timedelta(seconds=2, milliseconds=10 * i))
    assert w.kinds() == [BURST]


def test_each_kind_once_a_day_per_account_and_instrument(w):
    w.order(Side.BUY, price=2501000)
    w.order(Side.SELL, price=2500000)
    w.order(Side.SELL, price=2499000)
    assert w.kinds() == [SELF_TRADE]


def test_an_alert_is_kept_and_acknowledged_with_a_note(w):
    w.order(Side.BUY, price=2501000)
    w.order(Side.SELL, price=2500000)
    (a,) = w.s.alerts()
    assert w.notes[0][0] == "SURVEILLANCE" and w.notes[0][1] == "acc"
    with pytest.raises(ValueError, match="say what you found"):
        w.s.acknowledge(a.alert_id, "risk", "ok")
    done = w.s.acknowledge(a.alert_id, "risk", "a hedge leg, not a wash trade")
    assert (done.acked_by, done.ack_note) == ("risk", "a hedge leg, not a wash trade")
    assert w.s.alerts(open_only=True) == []
    with pytest.raises(ValueError, match="already acknowledged by risk"):
        w.s.acknowledge(a.alert_id, "risk", "again, twice")
    assert w.notes[-1][0] == "SURVEILLANCE_ACK"


def test_alerts_and_acknowledgements_come_back_after_a_restart(w):
    from mocktrade.risk.surveillance import alert_to_dict

    w.order(Side.BUY, price=2501000)
    w.order(Side.SELL, price=2500000)
    (a,) = w.s.alerts()
    w.s.acknowledge(a.alert_id, "risk", "a hedge leg, not a wash trade")
    again = Surveillance(clock=lambda: w.now)
    again.restore([alert_to_dict(a)], [{"alert_id": a.alert_id, "by": "risk",
                                        "note": "a hedge leg, not a wash trade"}])  # fmt: skip
    (back,) = again.alerts()
    assert (back.alert_id, back.kind, back.acked_by) == (a.alert_id, SELF_TRADE, "risk")
    assert again._raised  # not raised again today for the same thing


async def test_risk_sees_and_acknowledges_traders_dont_and_an_auditor_gets_the_day(
    tmp_path, nifty_fut
):
    from test_server import World as Server

    srv = Server(tmp_path, nifty_fut)
    ayush = await srv.logged_in("ayush")  # trader + admin
    riya = await srv.logged_in("riya")
    risk = await srv.logged_in("risk")
    for side, price in ((Side.BUY, 2501000), (Side.SELL, 2500000)):
        body = {"instrument_id": FUT, "side": side.value, "qty": 75, "order_type": "LIMIT",
                "product": "NRML", "price_paise": price}  # fmt: skip
        assert (await ayush.post("/api/accounts/acc-ayush/orders", json=body)).status_code == 201
    assert (await riya.get("/api/surveillance")).status_code == 403
    (alert,) = (await risk.get("/api/surveillance")).json()
    assert alert["kind"] == SELF_TRADE and alert["account_id"] == "acc-ayush"
    r = await risk.post(f"/api/surveillance/{alert['alert_id']}/ack",
                        json={"note": "checked: a hedge"})  # fmt: skip
    assert r.status_code == 200 and r.json()["acked_by"] == "risk"
    await srv.log.flush()
    url = "/api/accounts/acc-ayush/audit-export"
    orders_day = srv.clock.now.astimezone(srv_ist()).date().isoformat()  # the server's clock
    csv = await risk.get(url, params={"day": orders_day})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "SUBMITTED" in csv.text
    day = datetime.now(UTC).astimezone(srv_ist()).date().isoformat()  # notes: the real clock
    text = (await risk.get(url, params={"day": day})).text
    assert "SURVEILLANCE" in text and "SURVEILLANCE_ACK" in text
    assert (await riya.get("/api/accounts/acc-riya/audit-export",
                           params={"day": day})).status_code == 403  # fmt: skip
    assert (await risk.get(url, params={"day": "23-10-2026"})).status_code == 422
    for c in (ayush, riya, risk):
        await c.aclose()
    srv.log.close()


def srv_ist():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Kolkata")
