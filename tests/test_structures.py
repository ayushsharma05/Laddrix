"""Structure positions (#428): spreads held, their P&L, and what is left unallocated.

An Autospreader's fills go to its spread's structure at once; a trader can allocate, remove
and move fills by hand; every change is a note that rebuilds the book at start.

Calendar OCT − NOV (both lot 75). Hand-worked numbers:
    bought 2 calendars: OCT +150 at 25,010.00, NOV −150 at 25,140.00 → spread −130.00
    sold 1 back:        OCT −75 at 25,020.00,  NOV +75 at 25,140.00 → realised
                        (25,020.00 − 25,010.00) × 75 = ₹750 on OCT, 0 on NOV
"""

from types import SimpleNamespace

import pytest
from test_autospreader import AYUSH, NOV, OCT, W

from mocktrade.domain.orders import (
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Product,
    Side,
)
from mocktrade.orders.model import EventKind
from mocktrade.structures.book import StructureBook, StructureError, StructureLeg

CALENDAR = [StructureLeg(OCT, 1, 1), StructureLeg(NOV, -1, -1)]


class Notes:
    """The audit trail's notes, as the event log would keep them."""

    def __init__(self):
        self.items = []

    def __call__(self, kind, detail, extra):
        from datetime import UTC, datetime
        from types import SimpleNamespace

        self.items.append(SimpleNamespace(kind=kind, detail=detail, extra=extra,
                                          ts=datetime(2026, 10, 24, 5, tzinfo=UTC)))  # fmt: skip


@pytest.fixture
def w(nifty_fut):
    w = W(nifty_fut)
    w.notes = Notes()
    w.sb = book_for(w, w.notes)
    w.om.subscribe(w.sb.on_order_event)
    return w


def book_for(w, notes):
    def net(instrument_id):
        return sum(o.request.side.sign * o.filled_qty for o in w.om.orders()
                   if o.request.instrument_id == instrument_id)  # fmt: skip

    return StructureBook("acc-1", order=w.om.get, orders=w.om.orders, net_qty=net,
                         lot_size=lambda i: 75, ltp=lambda i: w.md.latest(i).ltp_paise,
                         spread_of=w.engine.spread_of, note=notes)  # fmt: skip


async def by_hand(w, instrument_id, side, qty, price):
    """An order placed by hand (no algo), filled at once."""
    request = OrderRequest("", "acc-1", "ayush", instrument_id, side, qty, OrderType.LIMIT,
                           Product.NRML, price_paise=price)  # fmt: skip
    view = await w.om.submit(request)
    w.om.apply(OrderUpdate(view.client_order_id, view.broker_order_id, OrderStatus.FILLED, qty,
                           price, last_fill_qty=qty, last_fill_price_paise=price))  # fmt: skip
    return view.client_order_id


async def trade_calendar(w, spread, lots, *, buy=True, q_price=2501000, h_price=2514000):
    algo = w.start(spread, -13000, buy=buy, lots=lots)
    await w.engine.settle()
    (q,) = [c for c in w.children(algo, OCT)]
    w.fill(q, 75 * lots, q_price)
    await w.engine.settle()
    (h,) = w.children(algo, NOV)
    w.fill(h, 75 * lots, h_price)
    return algo


async def test_an_autospreaders_fills_make_its_spreads_structure(w):
    spread = w.calendar()
    await trade_calendar(w, spread, 2)
    (s,) = w.sb.structures()
    assert (s["name"], s["spread_id"], s["lots"]) == ("Oct/Nov", spread.spread_id, 2)
    assert s["avg_spread_paise"] == 2501000 - 2514000
    assert [(g["qty"], g["avg_price_paise"], g["residual_qty"]) for g in s["legs"]] == [
        (150, 2501000, 0), (-150, 2514000, 0)]  # fmt: skip
    # marked at the last prices: OCT 25,010.00 (unchanged), NOV 25,140.00
    assert s["mark_paise"] == 2501000 - 2514000 and s["unrealised_paise"] == 0
    assert w.sb.unallocated() == [] and w.sb.free_fills() == []


async def test_a_partial_exit_leaves_the_rest_and_realises_its_pnl(w):
    spread = w.calendar()
    await trade_calendar(w, spread, 2)
    await trade_calendar(w, spread, 1, buy=False, q_price=2502000, h_price=2514000)
    (s,) = w.sb.structures()
    assert s["lots"] == 1
    assert [g["qty"] for g in s["legs"]] == [75, -75]
    assert s["realised_paise"] == (2502000 - 2501000) * 75  # ₹750 on OCT, nothing on NOV
    assert s["avg_spread_paise"] == 2501000 - 2514000  # the cost of what is left never resets


async def test_a_spread_half_done_shows_its_residual(w):
    spread = w.calendar()
    algo = w.start(spread, -13000, lots=2)
    await w.engine.settle()
    (q,) = w.children(algo, OCT)
    w.fill(q, 150, 2501000)
    await w.engine.settle()
    (h,) = w.children(algo, NOV)
    w.fill(h, 75, 2514000)  # one hedged, one not yet
    (s,) = w.sb.structures()
    assert s["lots"] == 1
    assert [(g["qty"], g["residual_qty"]) for g in s["legs"]] == [(150, 75), (-75, 0)]


async def test_fills_are_allocated_by_hand_never_twice(w):
    s = w.sb.create("by hand", CALENDAR, "ayush")
    oct_ = await by_hand(w, OCT, Side.BUY, 150, 2501000)
    nov = await by_hand(w, NOV, Side.SELL, 150, 2514000)
    assert [(f["client_order_id"], f["unallocated_qty"]) for f in w.sb.free_fills()] == [
        (oct_, 150), (nov, 150)]  # fmt: skip
    assert w.sb.unallocated() == [{"instrument_id": NOV, "qty": -150},
                                  {"instrument_id": OCT, "qty": 150}]  # fmt: skip
    w.sb.allocate(s.structure_id, oct_, 75, "ayush")
    w.sb.allocate(s.structure_id, nov, 75, "ayush")
    view = w.sb.get(s.structure_id)
    assert view["lots"] == 1
    assert w.sb.unallocated() == [{"instrument_id": NOV, "qty": -75},
                                  {"instrument_id": OCT, "qty": 75}]  # fmt: skip
    other = w.sb.create("other", CALENDAR, "ayush")
    too_many = f"{oct_} has 75 filled not yet in a structure, not 150"
    with pytest.raises(StructureError, match=too_many):
        w.sb.allocate(other.structure_id, oct_, 150, "ayush")
    with pytest.raises(StructureError, match="40 is not a whole number of lots"):
        w.sb.allocate(other.structure_id, oct_, 40, "ayush")
    dec = w.sb.create("dec only", [StructureLeg("NFO:NIFTY26DECFUT", 1, 1),
                                   StructureLeg(NOV, -1, -1)], "ayush")  # fmt: skip
    with pytest.raises(StructureError, match=f"{OCT} is not a leg of dec only"):
        w.sb.allocate(dec.structure_id, oct_, 75, "ayush")
    with pytest.raises(StructureError, match="no order NOPE in this account"):
        w.sb.allocate(s.structure_id, "NOPE", 75, "ayush")
    with pytest.raises(StructureError, match="by hand holds 75 of"):
        w.sb.remove(s.structure_id, oct_, 150, "ayush")
    w.sb.remove(s.structure_id, oct_, 75, "ayush")
    assert w.sb.get(s.structure_id)["lots"] == 0


async def test_splitting_and_combining_keep_every_total(w):
    spread = w.calendar()
    await trade_calendar(w, spread, 2)
    await trade_calendar(w, spread, 1, buy=False, q_price=2502000)
    (whole,) = w.sb.structures()

    def totals():
        views = w.sb.structures()
        legs = [sum(v["legs"][i]["qty"] for v in views) for i in range(2)]
        return legs, sum(v["realised_paise"] for v in views), sum(v["pnl_paise"] for v in views)

    before = totals()
    half = w.sb.create("half", CALENDAR, "ayush")
    for a in whole["allocations"]:
        if a["side"] == ("BUY" if a["instrument_id"] == OCT else "SELL"):  # the opening legs
            w.sb.move(whole["structure_id"], half.structure_id, a["client_order_id"], 75, "ayush")
    assert totals() == before  # split: the same legs, realised and P&L in all
    for a in w.sb.get(half.structure_id)["allocations"]:
        w.sb.move(half.structure_id, whole["structure_id"], a["client_order_id"], a["qty"],
                  "ayush")  # fmt: skip
    assert totals() == before  # combined back
    w.sb.delete(half.structure_id, "ayush")  # empty: it may go
    with pytest.raises(StructureError, match="Oct/Nov still holds fills"):
        w.sb.delete(whole["structure_id"], "ayush")
    with pytest.raises(StructureError, match="moving needs two different structures"):
        w.sb.move(whole["structure_id"], whole["structure_id"], "x", 75, "ayush")


async def test_the_book_is_rebuilt_from_its_notes_and_replays_add_nothing(w):
    spread = w.calendar()
    algo = await trade_calendar(w, spread, 2)
    s = w.sb.create("by hand", CALENDAR, "ayush")
    oct_ = await by_hand(w, OCT, Side.BUY, 75, 2501000)
    w.sb.allocate(s.structure_id, oct_, 75, "ayush")
    again = book_for(w, Notes())
    again.load(w.notes.items)
    assert again.structures() == w.sb.structures()
    (q,) = w.children(algo, OCT)
    view = w.om.get(q)
    w.om.apply(
        OrderUpdate(
            q,
            view.broker_order_id,
            OrderStatus.FILLED,
            150,
            2501000,
            last_fill_qty=150,
            last_fill_price_paise=2501000,
        )
    )  # replayed
    assert w.sb.structures()[0]["lots"] == 2
    kinds = [n.kind for n in w.notes.items]
    assert kinds.count("STRUCTURE_CREATED") == 2 and "STRUCTURE_ALLOCATED" in kinds


async def test_legs_pointing_opposite_ways_make_no_whole_spread(w):
    s = w.sb.create("odd", CALENDAR, "ayush")
    oct_ = await by_hand(w, OCT, Side.BUY, 75, 2501000)
    nov = await by_hand(w, NOV, Side.BUY, 75, 2514000)  # bought too: not a calendar
    w.sb.allocate(s.structure_id, oct_, 75, "ayush")
    w.sb.allocate(s.structure_id, nov, 75, "ayush")
    view = w.sb.get(s.structure_id)
    assert view["lots"] == 0 and view["note"].startswith("its legs point opposite ways")
    assert [g["residual_qty"] for g in view["legs"]] == [75, 75]


def test_a_structure_needs_a_name_and_legs(w):
    with pytest.raises(StructureError, match="the name must be 1 to 40 characters"):
        w.sb.create(" ", CALENDAR, "ayush")
    with pytest.raises(StructureError, match="a structure needs its legs"):
        w.sb.create("x", [], "ayush")
    with pytest.raises(StructureError, match="no structure nope") as missing:
        w.sb.get("nope")
    assert missing.value.missing
    assert AYUSH.id == "ayush"


async def test_structures_come_back_after_a_restart(tmp_path, nifty_fut):
    from test_algo_restore import Server, as_broker_lists

    from mocktrade.broker.fake import FakeBroker

    s = Server(tmp_path, nifty_fut, FakeBroker())
    await s.ready()
    made = s.runtime.structures.create("kept", CALENDAR, "ayush")
    broker = as_broker_lists(s)
    await s.stop()
    again = Server(tmp_path, nifty_fut, broker)
    await again.ready()
    assert [x["name"] for x in again.runtime.structures.structures()] == ["kept"]
    assert again.runtime.structures.get(made.structure_id)["structure_id"] == made.structure_id
    await again.stop()


async def test_the_api_for_structures(tmp_path, nifty_fut):
    from test_spread_api import CALENDAR as CAL
    from test_spread_api import world

    w, book = world(tmp_path, nifty_fut)
    ayush = await w.logged_in("ayush")
    sid = (await ayush.post("/api/spreads", json=CAL)).json()["spread_id"]
    url = "/api/accounts/acc-ayush/structures"
    made = await ayush.post(url, json={"spread_id": sid})
    assert made.status_code == 201, made.text
    s = made.json()
    assert (s["name"], s["lots"], [g["ratio"] for g in s["legs"]]) == ("NIFTY Oct/Nov", 0, [1, -1])
    listed = (await ayush.get(url)).json()
    assert [x["structure_id"] for x in listed["structures"]] == [s["structure_id"]]
    assert listed["unallocated"] == [] and listed["free_fills"] == []
    bad = await ayush.post(f"{url}/{s['structure_id']}/allocations",
                           json={"client_order_id": "NOPE", "qty": 75})  # fmt: skip
    assert (bad.status_code, bad.json()["detail"]) == (422, "no order NOPE in this account")
    gone = await ayush.post(f"{url}/zzz/allocations", json={"client_order_id": "x", "qty": 75})
    assert gone.status_code == 404
    assert (await ayush.post(url, json={"spread_id": "nope"})).status_code == 404
    riya = await w.logged_in("riya")
    assert (await riya.get(url)).status_code in (403, 404)  # not her account
    theirs = await riya.post("/api/accounts/acc-riya/structures", json={"spread_id": sid})
    assert theirs.status_code == 404  # not her spread
    assert (await ayush.delete(f"{url}/{s['structure_id']}")).status_code == 204
    assert (await ayush.get(url)).json()["structures"] == []
    await ayush.aclose()
    await riya.aclose()
    w.log.close()


async def test_an_auto_fill_taken_out_by_hand_is_not_put_back_by_a_replay(w):
    spread = w.calendar()
    algo = await trade_calendar(w, spread, 1)
    (s,) = w.sb.structures()
    (q,) = w.children(algo, OCT)
    w.sb.remove(s["structure_id"], q, 75, "ayush")  # the trader takes it out
    reloaded = book_for(w, Notes())
    reloaded.load(w.notes.items)
    view = w.om.get(q)
    for book in (w.sb, reloaded):
        book.on_order_event(SimpleNamespace(kind=EventKind.FILL, order=view))
        assert book.get(s["structure_id"])["legs"][0]["qty"] == 0  # not put back
    assert w.sb.free_fills()[0]["client_order_id"] == q


async def test_a_deleted_structure_stays_deleted_after_a_restart(w):
    s = w.sb.create("gone", CALENDAR, "ayush")
    w.sb.delete(s.structure_id, "ayush")
    reloaded = book_for(w, Notes())
    reloaded.load(w.notes.items)
    assert reloaded.structures() == []
