"""MTM breaches through failures and restarts (#263).

The failures before: which rules fired was kept in memory only and a rule fired before its
close finished, so a refused or failed close stayed silent for the rest of the day (the rule
had "fired"), and a restart forgot it had fired and could close again. A broken rules file
loaded nothing and only said so in the log. Now each firing is a saved breach whose state is
worked out from its orders and positions; a blocked close is shown with Retry; a restart
sends nothing by itself; unreadable data is kept aside and shown."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from test_mtm import Clock, flat, move
from test_portfolio import FUT, Paper

from laddrix.domain.orders import Product, Side
from laddrix.ops.alerts import Alerts
from laddrix.ops.guards import DayGuards
from laddrix.positions.reconcile import Mismatch
from laddrix.risk.mtm import MtmWatch, PortfolioRule, PositionRule

KEY = f"{FUT}|MIS"


@pytest.fixture
async def day(nifty_fut, tmp_path):
    clock = Clock()
    p = Paper(nifty_fut, tmp_path, clock=clock)
    p.clock = clock
    p.path = tmp_path / "rules" / "mtm-acc.json"
    yield p
    await p.stop()


def watch(p, *, wired=True, **kw):
    """An MTM watch on the paper account, saved to disk, wired like the platform does."""
    w = MtmWatch("acc", p.rt.positions, p.rt.orders, p.store.get, max_lots=10, path=p.path,
                 clock=p.clock, **kw)  # fmt: skip
    if wired:
        p.rt.positions.subscribe(w.check)
        p.rt.orders.subscribe(w.on_order_event)
    return w


def closes(p):
    return [o for o in p.rt.orders.orders() if (o.request.basket_id or "").startswith("MTM")]


def refuse_closes(p, reason="price band: too far from the last price"):
    """The risk gate refuses MTM closes until the returned function is called."""
    real = p.rt.risk.admit_new

    async def admit(request):
        if (request.basket_id or "").startswith("MTM"):
            return reason
        return await real(request)

    p.rt.risk.admit_new = admit
    return lambda: setattr(p.rt.risk, "admit_new", real)


async def breached(p, w, *, auto_close=True):
    await p.buy(FUT, 150, 2501010, Product.MIS)
    w.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00,
                                     auto_close=auto_close), "ayush")  # fmt: skip
    await move(p, 2499000)  # −₹3,015: past the ₹1,000 limit
    await w.settle()
    await asyncio.sleep(0.05)
    (b,) = w.breaches.values()
    return b


async def test_a_refused_close_stays_blocked_on_screen_and_a_retry_closes_it(day):
    p = day
    allow = refuse_closes(p)
    w = watch(p)
    b = await breached(p, w)
    assert b.state == "BLOCKED"
    assert b.detail.startswith(f"{FUT} MIS +150 still open: refused (")
    assert b.detail.endswith("price band: too far from the last price); retry when ready")
    shown = w.to_dict()["breaches"]
    assert [(x["key"], x["state"]) for x in shown] == [(KEY, "BLOCKED")]
    alerts = Alerts()
    p.platform.accounts["acc"].mtm = w  # the guards look at the account's watch
    guards = DayGuards(p.platform, alerts)
    guards.check_mtm()
    (alert,) = [a for a in alerts.active() if a.key.startswith("mtm-close:")]
    assert alert.text.startswith(f"acc MTM {KEY}: close blocked: {FUT} MIS +150 still open")
    await w.retry(KEY, "ayush")  # refused again: still blocked, naming the new order
    second = closes(p)[-1].client_order_id
    assert b.state == "BLOCKED" and f"{second}: price band" in b.detail
    allow()
    await w.retry(KEY, "ayush")
    await flat(p, product=Product.MIS)
    w.follow()
    assert (b.state, b.detail, b.attempts) == ("COMPLETED", "closed: nothing left open", 3)
    guards.check_mtm()
    assert not [a for a in alerts.active() if a.key.startswith("mtm-close:")]


async def test_only_a_blocked_close_can_be_retried(day):
    p = day
    w = watch(p)
    b = await breached(p, w)
    await flat(p, product=Product.MIS)
    w.follow()
    assert b.state == "COMPLETED"
    with pytest.raises(ValueError, match="only a blocked close can be retried"):
        await w.retry(KEY, "ayush")


async def test_a_close_that_fails_outright_is_blocked_not_silent(day):
    p = day
    w = watch(p)

    class Broken:
        async def close(self, *_, **__):
            raise RuntimeError("the desk fell over")

    w._desk = Broken()
    b = await breached(p, w)
    assert b.state == "BLOCKED"
    assert b.detail == (f"{FUT} MIS +150 still open: nothing was sent (a failure, or a restart "
                        "while closing); retry when ready")  # fmt: skip


async def test_a_restart_during_a_close_never_closes_twice(day):
    p = day
    w = watch(p)
    allow = refuse_closes(p, "(not yet)")
    allow()  # closes go through
    b = await breached(p, w)
    await flat(p, product=Product.MIS)
    sent = len(closes(p))
    assert sent == 1
    # the server restarts: the rules, the firing and the breach come back from disk
    again = watch(p)
    assert set(again.breaches) == {b.id}
    await move(p, 2490000)  # still past the limit: it must not fire again today
    await move(p, 2489000)
    await again.settle()
    assert len(closes(p)) == sent
    again.follow()
    assert again.breaches[b.id].state == "COMPLETED"


async def test_a_restart_before_anything_was_sent_is_blocked_not_resent(day):
    p = day
    allow = refuse_closes(p)
    w = watch(p)
    b = await breached(p, w)
    state = p.path.with_name("mtm-acc-state.json")
    data = json.loads(state.read_text())
    data["breaches"][0].update(state="REQUESTED", baskets=["MTM999999"])  # died mid-close
    state.write_text(json.dumps(data))
    allow()
    before = len(closes(p))
    again = watch(p)
    await move(p, 2490000)
    await again.settle()
    assert again.breaches[b.id].state == "BLOCKED" and len(closes(p)) == before
    assert "nothing was sent" in again.breaches[b.id].detail
    assert not [o for o in closes(p) if o.request.basket_id == "MTM999999"]


async def test_nothing_is_judged_before_the_restart_has_restored_orders_and_positions(day):
    p = day
    w = watch(p)
    b = await breached(p, w, auto_close=False)
    assert b.state == "ALERTED"
    ready = [False]
    again = watch(p, ready=lambda: ready[0])
    again.positions[KEY] = PositionRule(FUT, "MIS", max_loss_paise=1, auto_close=True)
    again._fired.clear()  # even a rule that would fire waits until the restore is done
    assert again.check() == []
    ready[0] = True
    assert len(again.check()) == 1


async def test_done_only_when_the_broker_agrees_on_the_size(day):
    p = day
    unknown = {(FUT, Product.MIS): Mismatch(FUT, 0, 75, datetime.now(UTC), Product.MIS).message}
    w = watch(p, unknown=lambda i, prod: unknown.get((i, prod)))
    b = await breached(p, w)
    await flat(p, product=Product.MIS)  # our book says closed; the broker says +75
    w.follow()
    assert b.state == "UNKNOWN" and "the broker shows +75" in b.detail
    unknown.clear()
    w.follow()
    assert b.state == "COMPLETED"


async def test_an_unanswered_close_order_is_unknown_never_retried(day):
    from laddrix.orders.model import Pending

    p = day
    w = watch(p)
    allow = refuse_closes(p)
    b = await breached(p, w)
    allow()
    order = p.rt.orders._orders[closes(p)[0].client_order_id]
    order.pending = Pending.UNCONFIRMED  # as if the broker never answered it
    w.follow()
    assert b.state == "UNKNOWN" and "hasn't answered" in b.detail
    with pytest.raises(ValueError):
        await w.retry(KEY, "ayush")


async def test_the_portfolio_stop_is_a_breach_too(day):
    p = day
    w = watch(p)
    await p.buy(FUT, 150, 2501010, Product.MIS)
    w.set_portfolio_rule(PortfolioRule(max_loss_paise=1_000_00, auto_close=True), "ayush")
    await move(p, 2499000)
    await w.settle()
    await flat(p)
    w.follow()
    (b,) = w.breaches.values()
    assert (b.kind, b.key, b.targets, b.state) == ("portfolio", "portfolio",
                                                   [[FUT, "MIS"]], "COMPLETED")  # fmt: skip


# --- data that can't be read is kept and shown, never silently dropped ---------------------------


async def test_a_broken_rules_file_is_kept_aside_and_shown(day):
    p = day
    p.path.parent.mkdir(parents=True)
    p.path.write_text("{half a file")
    w = watch(p)
    assert w.positions == {} and "NO loss limits are watched for acc" in w.problem
    kept = list(p.path.parent.glob("mtm-acc.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text() == "{half a file"
    assert not p.path.exists()  # the next save can't overwrite what a person must look at
    w.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=100), "ayush")
    assert w.problem == "" and kept[0].exists()


async def test_a_bad_rule_is_named_and_the_good_ones_still_watched(day):
    p = day
    p.path.parent.mkdir(parents=True)
    p.path.write_text(json.dumps({"positions": [
        {"instrument_id": FUT, "product": "MIS", "max_loss_paise": 100},
        {"instrument_id": "NSE:RELIANCE", "product": "MIS", "max_loss_paise": -5},
    ], "portfolio": {"max_loss_paise": None, "target_paise": None}}))  # fmt: skip
    w = watch(p)
    assert list(w.positions) == [KEY] and w.portfolio is None
    assert "2 saved MTM limit(s) can't be used and are NOT watched" in w.problem
    assert "NSE:RELIANCE: the loss limit must be above 0" in w.problem
    assert "portfolio: give a loss stop or a profit target" in w.problem
    assert list(p.path.parent.glob("mtm-acc.unreadable-*.json"))
    alerts = Alerts()
    p.platform.accounts["acc"].mtm = w
    DayGuards(p.platform, alerts)  # said at once, at any hour
    assert [a.key for a in alerts.active()] == ["mtm-data:acc"]


async def test_a_broken_state_file_holds_auto_close_for_the_day(day):
    p = day
    w = watch(p, wired=False)  # (only to save the rule)
    w.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00, auto_close=True), "a")
    p.path.with_name("mtm-acc-state.json").write_text("[]garbage")
    again = watch(p)
    assert "auto-close is held for today so nothing closes twice" in again.problem
    await p.buy(FUT, 150, 2501010, Product.MIS)
    await move(p, 2499000)
    await again.settle()
    assert closes(p) == []  # it can't know whether it already closed today: it doesn't


async def test_saves_are_whole_files_and_keep_only_what_matters(day):
    p = day
    w = watch(p)
    await breached(p, w)
    folder = p.path.parent
    assert not list(folder.glob("*.tmp"))
    state = json.loads((folder / "mtm-acc-state.json").read_text())
    assert state["fired"] == {KEY: "2026-10-16"}
    assert [b["state"] for b in state["breaches"]] in (["WORKING"], ["COMPLETED"], ["REQUESTED"])


class SlowDesk:
    """An exit desk that rests a LIMIT close (it doesn't fill) and can be held mid-close."""

    def __init__(self, p, *, hold=False):
        self.p, self.release, self.seen = p, asyncio.Event(), []
        if not hold:
            self.release.set()

    async def close(self, instrument, product, *, basket_id, **_):
        from laddrix.domain.orders import OrderRequest, OrderType, Side

        state = self.p.path.with_name("mtm-acc-state.json")
        self.seen.append(json.loads(state.read_text())["breaches"][0]["state"])
        await self.release.wait()
        request = OrderRequest("", "acc", "mtm", instrument.id, Side.SELL, 150, OrderType.LIMIT,
                               product, price_paise=2510000, basket_id=basket_id,
                               reduce_only=True)  # fmt: skip
        await self.p.rt.orders.submit(request)
        from laddrix.risk.closing import CloseResult

        return CloseResult(instrument.id, product, position=150)


async def test_the_close_is_saved_as_requested_before_anything_is_sent_and_shown_meanwhile(day):
    p = day
    w = watch(p)
    w._desk = desk = SlowDesk(p, hold=True)
    await p.buy(FUT, 150, 2501010, Product.MIS)
    w.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00, auto_close=True), "a")
    await move(p, 2499000)
    await asyncio.sleep(0.05)
    (b,) = w.breaches.values()
    assert desk.seen == ["REQUESTED"]  # on disk before the desk was called
    w.follow()
    assert (b.state, b.detail) == ("REQUESTED", f"closing: {FUT} MIS +150 open")  # not blocked
    desk.release.set()
    await w.settle()
    assert b.state == "WORKING" and b.detail.endswith("working")


async def test_a_working_close_cancelled_later_is_blocked_through_the_platforms_wiring(day):
    p = day
    mtm = p.rt.mtm  # the platform's own watch, wired to the order manager
    mtm._desk = SlowDesk(p)
    mtm._path = p.path
    await p.buy(FUT, 150, 2501010, Product.MIS)
    mtm.set_position_rule(PositionRule(FUT, "MIS", max_loss_paise=1_000_00, auto_close=True), "a")
    await move(p, 2499000)
    await mtm.settle()
    (b,) = mtm.breaches.values()
    assert b.state == "WORKING"
    (close,) = closes(p)
    await p.rt.orders.cancel(close.client_order_id)  # the position doesn't move: only the order
    await asyncio.sleep(0.1)
    assert b.state == "BLOCKED" and "its orders ended without closing it" in b.detail


async def test_a_completed_close_stays_completed_when_the_trader_trades_again(day):
    p = day
    w = watch(p)
    b = await breached(p, w)
    await flat(p, product=Product.MIS)
    w.follow()
    assert b.state == "COMPLETED"
    await p.buy(FUT, 75, 2501010, Product.MIS)  # a new position, after the close
    w.follow()
    assert b.state == "COMPLETED"


async def test_nothing_is_marked_done_before_the_restore_is_finished(day):
    p = day
    w = watch(p)
    w._desk = SlowDesk(p)
    b = await breached(p, w)
    assert b.state == "WORKING"
    ready = [False]
    again = watch(p, wired=False, ready=lambda: ready[0])
    for o in closes(p):
        await p.rt.orders.cancel(o.client_order_id)
    await p.buy(FUT, 150, 2490000, Product.MIS, side=Side.SELL)  # closed by hand meanwhile
    again.follow()  # flat now, but the restore isn't done: no verdict yet
    assert again.breaches[b.id].state == "WORKING"
    ready[0] = True
    again.follow()
    assert again.breaches[b.id].state == "COMPLETED"


async def test_an_alert_only_firing_is_remembered_across_a_restart(day):
    p = day
    w = watch(p)
    b = await breached(p, w, auto_close=False)
    again = watch(p, wired=False)
    assert again.breaches[b.id].state == "ALERTED"
    assert again.to_dict()["positions"][0]["fired_today"] is True  # not alerted twice today
