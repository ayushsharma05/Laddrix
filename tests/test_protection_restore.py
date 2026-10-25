"""Lost stops across restarts, and restoring one safely (#264).

The failure before: a lost stop was reported once (ALGO_LOST) and then forgotten, so after a
second restart an open position was unprotected with no band. Now every flag and resolution
is a note in the audit trail and comes back on each start until resolved; a person may restore
the stop, only when that can't duplicate an exit."""

import pytest
from test_protect import FUT, tick

from laddrix.algos.builtin import BUILTIN
from laddrix.broker.fake import FakeBroker
from laddrix.domain.accounts import BrokerAccount
from laddrix.domain.orders import OrderRequest, OrderType, Product, Side
from laddrix.marketdata.service import MarketData
from laddrix.ops.alerts import Alerts
from laddrix.ops.protection import ProtectionGuard, RestoreError
from laddrix.orders.model import ClientIds, Pending
from laddrix.platform import Platform
from laddrix.refdata.store import InstrumentStore
from laddrix.risk.kill_switch import KillSwitch
from laddrix.store.events import EventLog

NRML = Product.NRML


def boot(tmp_path, nifty_fut, held=0, price=2501000):
    store, md = InstrumentStore(), MarketData()
    store.add(nifty_fut)
    md.on_tick(tick(2501000))
    log = EventLog(tmp_path / "events.db")
    log.start()
    p = Platform(store, md, KillSwitch("firm"), log)
    rt = p.add_account(BrokerAccount("acc", "paper", "P1", "ayush"), FakeBroker(),
                       ids=ClientIds("MT"), algo_types=BUILTIN)  # fmt: skip
    if held:
        side = Side.BUY if held > 0 else Side.SELL
        rt.positions.apply_fill(FUT, NRML, side, abs(held), price)
    guard = ProtectionGuard(p, Alerts())
    return p, rt, log, guard


async def lose_a_stop(tmp_path, nifty_fut, code="TRAILING_STOP", side=Side.SELL, qty=150,
                      params=None):  # fmt: skip
    """A stop runs; the server stops; it starts again with the position still open."""
    p, rt, log, _ = boot(tmp_path, nifty_fut)
    rt.algos.start(code, user_id="ayush", instrument_id=FUT, side=side, qty=qty,
                   params=params or {"trail_paise": 2000})  # fmt: skip
    await rt.algos.settle()
    await log.aclose()
    p, rt, log, guard = boot(tmp_path, nifty_fut, held=150)
    guard.after_restart()
    return p, rt, log, guard


async def test_a_lost_stop_stays_flagged_across_every_restart_until_resolved(tmp_path, nifty_fut):
    p, rt, log, guard = await lose_a_stop(tmp_path, nifty_fut)
    assert list(guard.flagged()) == [("acc", FUT)]
    await log.aclose()
    p, rt, log, guard = boot(tmp_path, nifty_fut, held=150)  # a second restart
    guard.after_restart()
    assert list(guard.flagged()) == [("acc", FUT)]  # before #264: forgotten here
    rt.positions.apply_fill(FUT, NRML, Side.SELL, 150, 2501000)  # closed by hand
    guard.check()
    assert guard.flagged() == {}
    await log.aclose()
    p, rt, log, guard = boot(tmp_path, nifty_fut, held=150)
    guard.after_restart()
    assert guard.flagged() == {}  # resolved stays resolved
    await log.aclose()


async def test_restore_starts_the_same_stop_for_what_is_open_once(tmp_path, nifty_fut):
    p, rt, log, guard = await lose_a_stop(tmp_path, nifty_fut)
    rt.positions.apply_fill(FUT, NRML, Side.SELL, 75, 2501000)  # half closed meanwhile
    (row,) = guard.status("acc")
    assert row["blocked_by"] is None
    assert row["restore_as"].startswith("TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT NRML")
    view = guard.restore("acc", FUT, "ayush")
    assert (view.code, view.side, view.qty, view.product) == ("TRAILING_STOP", Side.SELL, 75,
                                                              NRML)  # fmt: skip
    assert guard.flagged() == {}
    with pytest.raises(RestoreError, match="isn't flagged"):
        guard.restore("acc", FUT, "ayush")
    await log.aclose()
    p, rt, log, guard = boot(tmp_path, nifty_fut, held=75)
    guard.after_restart()
    (why,) = guard.flagged().values()  # the old flag stays resolved; the RESTORED stop is
    assert "TRAILING_STOP SELL 75" in why  # itself lost by this restart, and says so
    await log.aclose()


async def test_restore_is_refused_while_an_old_order_is_working_or_unanswered(tmp_path, nifty_fut):
    p, rt, log, guard = await lose_a_stop(tmp_path, nifty_fut)
    orphan = await rt.orders.submit(OrderRequest("", "acc", "ayush", FUT, Side.SELL, 75,
                                                 OrderType.LIMIT, NRML, price_paise=2510000,
                                                 parent_id="A1"))  # fmt: skip
    with pytest.raises(RestoreError, match=f"{orphan.client_order_id} of the old stop is still"):
        guard.restore("acc", FUT, "ayush")
    rt.orders._orders[orphan.client_order_id].pending = Pending.UNCONFIRMED
    with pytest.raises(RestoreError, match="hasn't been answered by the broker"):
        guard.restore("acc", FUT, "ayush")
    await log.aclose()


async def test_restore_is_refused_when_another_stop_is_there_or_the_position_flipped(
    tmp_path, nifty_fut
):
    p, rt, log, guard = await lose_a_stop(tmp_path, nifty_fut)
    paused = rt.algos.start("OCO", user_id="ayush", instrument_id=FUT, side=Side.SELL, qty=150,
                            params={"target_paise": 2520000, "stop_paise": 2490000})  # fmt: skip
    rt.algos.pause(paused.algo_id)
    guard._flags[("acc", FUT)] = "lost"  # (pausing re-flags it; keep the original record)
    with pytest.raises(RestoreError, match="resume or stop it instead"):
        guard.restore("acc", FUT, "ayush")
    rt.algos.stop(paused.algo_id)
    await rt.algos.settle()
    rt.positions.apply_fill(FUT, NRML, Side.SELL, 300, 2501000)  # now short 150
    with pytest.raises(RestoreError, match="no NRML position for a SELL stop"):
        guard.restore("acc", FUT, "ayush")
    await log.aclose()


async def test_a_lost_bracket_comes_back_as_an_oco_at_its_levels(tmp_path, nifty_fut):
    p, rt, log, guard = await lose_a_stop(
        tmp_path,
        nifty_fut,
        code="BRACKET",
        side=Side.BUY,
        qty=150,
        params={"entry_paise": 2500000, "target_points_paise": 3000, "stop_points_paise": 2000},
    )
    (row,) = guard.status("acc")
    assert row["restore_as"] == ("OCO SELL 150 NFO:NIFTY26OCTFUT NRML "
                                 "{'target_paise': 2504000, 'stop_paise': 2499000}")  # fmt: skip
    await log.aclose()


async def test_without_knowing_which_stop_it_had_restore_says_to_start_one_by_hand(
    tmp_path, nifty_fut
):
    p, rt, log, guard = boot(tmp_path, nifty_fut, held=75)
    guard._flags[("acc", FUT)] = "its stop failed"  # flagged, but its stop isn't on record
    (row,) = guard.status("acc")
    assert row["restore_as"] is None
    assert row["blocked_by"] == "which stop it had isn't known: start a stop for it by hand"
    await log.aclose()
