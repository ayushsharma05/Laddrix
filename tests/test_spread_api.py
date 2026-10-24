"""Spreads through the server (#414): each user's own definitions, the spread as an
instrument, and its implied price streamed to its owner only."""

import json
from datetime import date

from test_server import ORIGIN, World
from test_stream import Live, next_of

from mocktrade.algos.autospreader import algo_type as ase_type
from mocktrade.domain.instruments import Exchange, Instrument, InstrumentKind
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.spreads.store import SpreadBook

FUT = "NFO:NIFTY26OCTFUT"
NOV = "NFO:NIFTY26NOVFUT"
CALENDAR = {
    "name": "NIFTY Oct/Nov",
    "legs": [
        {"instrument_id": FUT, "ratio": 1, "multiplier": 1, "quote": True, "payup_ticks": 2},
        {"instrument_id": NOV, "ratio": -1, "multiplier": -1},
    ],
}


def nov() -> Instrument:
    return Instrument(Exchange.NFO, "NIFTY26NOVFUT", InstrumentKind.FUTURE, tick_paise=10,
                      lot_size=75, expiry=date(2026, 11, 24), underlying="NIFTY")  # fmt: skip


def world(tmp_path, nifty_fut) -> tuple[World, SpreadBook]:
    w = World(tmp_path, nifty_fut)
    w.platform.instruments.add(nov())
    book = SpreadBook(tmp_path / "spreads", w.platform.instruments.get)
    w.app = create_app(w.platform, w.users, ServerSettings(origin=ORIGIN), spreads=book,
                       monotonic=w.clock.mono)  # fmt: skip
    return w, book


async def test_a_user_defines_lists_changes_and_deletes_their_spreads(tmp_path, nifty_fut):
    w, book = world(tmp_path, nifty_fut)
    c = await w.logged_in("ayush")
    made = await c.post("/api/spreads", json=CALENDAR)
    assert made.status_code == 201, made.text
    spread = made.json()
    assert spread["name"] == "NIFTY Oct/Nov" and spread["tick_paise"] == 10
    assert spread["instrument_id"] == f"SPREAD:{spread['spread_id']}"
    assert [x["ratio"] for x in spread["legs"]] == [1, -1]
    assert (await c.get("/api/spreads")).json() == [spread]
    # kept on disk at once: a restart finds it
    again = SpreadBook(tmp_path / "spreads", w.platform.instruments.get)
    again.load()
    assert [s.name for s in again.mine(w.users.get_user("ayush"))] == ["NIFTY Oct/Nov"]

    fly = {**CALENDAR, "name": "renamed"}
    changed = await c.put(f"/api/spreads/{spread['spread_id']}", json=fly)
    assert changed.status_code == 200 and changed.json()["name"] == "renamed"
    assert (await c.delete(f"/api/spreads/{spread['spread_id']}")).status_code == 204
    assert (await c.get("/api/spreads")).json() == []
    assert (await c.delete(f"/api/spreads/{spread['spread_id']}")).status_code == 404
    await c.aclose()
    w.log.close()


async def test_problems_are_said_in_words(tmp_path, nifty_fut):
    w, _ = world(tmp_path, nifty_fut)
    c = await w.logged_in("ayush")
    bad = {"name": "", "legs": [{**CALENDAR["legs"][0], "quote": False}, CALENDAR["legs"][1]]}
    r = await c.post("/api/spreads", json=bad)
    assert r.status_code == 422
    one_leg = "choose exactly one leg to quote (quoting several legs at once comes with #251)"
    assert r.json()["detail"] == f"the name must be 1 to 40 characters; {one_leg}"
    extra = await c.post("/api/spreads", json={**CALENDAR, "owner": "riya"})
    assert extra.status_code == 422  # unknown fields are refused, never ignored
    await c.aclose()
    w.log.close()


async def test_only_the_owner_sees_or_changes_a_spread(tmp_path, nifty_fut):
    w, _ = world(tmp_path, nifty_fut)
    ayush = await w.logged_in("ayush")
    spread = (await ayush.post("/api/spreads", json=CALENDAR)).json()
    riya = await w.logged_in("riya")
    assert (await riya.get("/api/spreads")).json() == []
    sid = spread["spread_id"]
    assert (await riya.put(f"/api/spreads/{sid}", json=CALENDAR)).status_code == 404
    assert (await riya.delete(f"/api/spreads/{sid}")).status_code == 404
    assert (await riya.get(f"/api/instruments/SPREAD:{sid}")).status_code == 404
    mine = await ayush.get(f"/api/instruments/SPREAD:{sid}")
    assert mine.status_code == 200
    assert mine.json() | {"ltp_paise": None} == {
        "id": f"SPREAD:{sid}", "exchange": "SPREAD", "symbol": "NIFTY Oct/Nov", "kind": "SPREAD",
        "tick_paise": 10, "lot_size": 1, "expiry": None, "strike_paise": None,
        "underlying": None, "tradable": True, "expiry_info": None, "ltp_paise": None,
        "spread": {"spread_id": sid, "legs": spread["legs"]},
    }  # fmt: skip
    await ayush.aclose()
    await riya.aclose()
    w.log.close()


async def test_the_owner_streams_the_implied_price_and_no_one_else_can(tmp_path, nifty_fut):
    live = Live(tmp_path, nifty_fut)
    live.store.add(nov())
    book = SpreadBook(None, live.store.get)
    live.app = create_app(live.platform, live.users,
                          ServerSettings(origin=live.origin, cookie_secure=False),
                          spreads=book)  # fmt: skip
    live.hub = live.app.state.hub
    live.hub.flush_s = 0.05
    await live.start()
    try:
        ayush = await live.session("ayush")
        sid = (await ayush.post("/api/spreads", json=CALENDAR)).json()["spread_id"]
        async with live.stream(ayush) as ws:
            await next_of(ws, "hello")
            await ws.send(json.dumps({"op": "subscribe", "instruments": [f"SPREAD:{sid}"]}))
            live.md.on_tick(Tick(FUT, 2501000, bids=(DepthLevel(2500900, 750, 3),),
                                 asks=(DepthLevel(2501000, 150, 1),)))  # fmt: skip
            live.md.on_tick(Tick(NOV, 2516000, bids=(DepthLevel(2515900, 300, 2),),
                                 asks=(DepthLevel(2516100, 750, 4),)))  # fmt: skip
            spread = None
            while spread is None or not spread["asks"] or not spread["bids"]:
                batch = await next_of(ws, "ticks")
                spread = next((t for t in batch["ticks"] if t["instrument_id"] == f"SPREAD:{sid}"),
                              spread)  # fmt: skip
            # buy: OCT ask 25010.00 − NOV bid 25159.00; 2 lots (150 / 75); sell: 25009 − 25161
            assert spread["asks"][0][:2] == [2501000 - 2515900, 2]
            assert spread["bids"][0][:2] == [2500900 - 2516100, 10]
        riya = await live.session("riya")
        async with live.stream(riya) as ws:
            await next_of(ws, "hello")
            await ws.send(json.dumps({"op": "subscribe", "instruments": [f"SPREAD:{sid}"]}))
            error = await next_of(ws, "error")
            assert error["message"] == f"unknown instruments: ['SPREAD:{sid}']"
        await ayush.aclose()
        await riya.aclose()
    finally:
        await live.stop()


async def test_trading_a_spread_starts_the_ase_algo_on_its_quoting_leg(tmp_path, nifty_fut):
    w, book = world(tmp_path, nifty_fut)
    for running in w.platform.accounts.values():  # the servers add it to every account
        running.algos.types["ASE"] = ase_type(book.get)
    ayush = await w.logged_in("ayush")
    quoting_nov = {**CALENDAR, "legs": [{**CALENDAR["legs"][0], "quote": False},
                                        {**CALENDAR["legs"][1], "quote": True}]}  # fmt: skip
    sid = (await ayush.post("/api/spreads", json=quoting_nov)).json()["spread_id"]
    order = {"side": "BUY", "lots": 2, "price_paise": -13000}
    r = await ayush.post(f"/api/accounts/acc-ayush/spreads/{sid}/orders", json=order)
    assert r.status_code == 201, r.text
    algo = r.json()
    # buying OCT − NOV sells NOV, the quoting leg: 2 spread lots of 1 lot of 75
    assert (algo["code"], algo["instrument_id"], algo["side"], algo["qty"]) == (
        "ASE", NOV, "SELL", 150)  # fmt: skip
    assert algo["params"] == {"spread": sid, "level_paise": -13000}

    ratio_two = {"name": "1:2", "legs": [{**CALENDAR["legs"][0], "quote": False},
                                         {**CALENDAR["legs"][1], "ratio": -2, "multiplier": -2,
                                          "quote": True}]}  # fmt: skip
    two = (await ayush.post("/api/spreads", json=ratio_two)).json()["spread_id"]
    r = await ayush.post(f"/api/accounts/acc-ayush/spreads/{two}/orders", json={**order, "lots": 1})
    assert (r.json()["side"], r.json()["qty"]) == ("SELL", 150)  # one spread lot: 2 lots of NOV

    bad = await ayush.post(
        f"/api/accounts/acc-ayush/spreads/{sid}/orders", json={**order, "lots": 0}
    )
    assert bad.status_code == 422
    riya = await w.logged_in("riya")
    theirs = await riya.post(f"/api/accounts/acc-riya/spreads/{sid}/orders", json=order)
    assert theirs.status_code == 404  # not her spread
    not_hers = await riya.post(f"/api/accounts/acc-ayush/spreads/{sid}/orders", json=order)
    assert not_hers.status_code in (403, 404)  # not her account
    await ayush.aclose()
    await riya.aclose()
    w.log.close()


async def test_controls_are_set_with_the_spread_and_published_for_the_screens(tmp_path, nifty_fut):
    w, book = world(tmp_path, nifty_fut)
    c = await w.logged_in("ayush")
    spec = (await c.get("/api/spreads/spec")).json()
    assert [p["name"] for p in spec["controls"]] == [
        "requote_ticks", "max_unhedged_lots", "chase_ticks", "hedge_timeout_s"]  # fmt: skip
    assert spec["controls"][0] | {"help": ""} == {
        "name": "requote_ticks", "label": "Re-quote threshold", "kind": "ticks", "help": "",
        "required": False, "default": 1, "min": 1, "max": 50, "choices": []}  # fmt: skip
    made = (await c.post("/api/spreads", json=CALENDAR)).json()
    assert made["controls"] == {"requote_ticks": 1, "max_unhedged_lots": 0, "chase_ticks": 0,
                                "hedge_timeout_s": 5.0}  # fmt: skip
    sid = made["spread_id"]
    tuned = {**CALENDAR, "controls": {"chase_ticks": 3, "hedge_timeout_s": 8}}
    changed = (await c.put(f"/api/spreads/{sid}", json=tuned)).json()
    assert changed["controls"]["chase_ticks"] == 3 and changed["controls"]["hedge_timeout_s"] == 8
    renamed = (await c.put(f"/api/spreads/{sid}", json={**CALENDAR, "name": "x"})).json()
    assert renamed["controls"]["chase_ticks"] == 3  # an edit without controls keeps them
    bad = await c.post("/api/spreads", json={**CALENDAR, "controls": {"chase_ticks": 99}})
    assert (bad.status_code, bad.json()["detail"]) == (422, "Chase must be at most 50")
    await c.aclose()
    w.log.close()


async def test_an_algos_actions_are_previewed_then_done_as_previewed(tmp_path, nifty_fut):
    w, book = world(tmp_path, nifty_fut)
    for running in w.platform.accounts.values():
        running.algos.types["ASE"] = ase_type(book.get)
    ayush = await w.logged_in("ayush")
    sid = (await ayush.post("/api/spreads", json=CALENDAR)).json()["spread_id"]
    algo = (await ayush.post(f"/api/accounts/acc-ayush/spreads/{sid}/orders",
                             json={"side": "BUY", "lots": 1, "price_paise": -13000})).json()  # fmt: skip
    assert [a["label"] for a in algo["actions"]] == ["Hedge now", "Flatten"]
    url = f"/api/accounts/acc-ayush/algos/{algo['algo_id']}/actions"
    preview = (await ayush.get(f"{url}/flatten")).json()
    assert preview == {"name": "flatten", "label": "Flatten", "lines": [], "ok": False,
                       "why": "nothing is unhedged: use Stop"}  # fmt: skip
    refused = await ayush.post(f"{url}/flatten", json={"expect": []})
    assert (refused.status_code, refused.json()["detail"]) == (409, "nothing is unhedged: use Stop")
    assert (await ayush.get(f"{url}/dance")).status_code == 404
    assert (await ayush.get("/api/accounts/acc-ayush/algos/A999/actions/flatten")).status_code == 404
    riya = await w.logged_in("riya")
    assert (await riya.get(f"{url}/flatten")).status_code in (403, 404)  # not her account
    await ayush.aclose()
    await riya.aclose()
    w.log.close()
