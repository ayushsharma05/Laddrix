"""The unprotected-position band (V1.2, #328): a position that had a stop held by Laddrix and
lost it (a restart, a pause, a stop, a failure) gets one ERROR band until it is flat or covered
again; never-protected positions are left alone; nothing is decided before recovery."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from laddrix.algos.engine import AlgoEvent, AlgoState, AlgoView
from laddrix.algos.spec import AlgoSpec
from laddrix.domain.gtt import GttStatus
from laddrix.domain.orders import OrderType, Side
from laddrix.ops.alerts import Alerts
from laddrix.ops.protection import ProtectionGuard
from laddrix.platform import LostProtection

FUT = "NFO:NIFTY26OCTFUT"
REL = "NSE:RELIANCE"
T0 = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


def spec(code, family):
    return AlgoSpec(code=code, name=code, help="", params=(), family=family)


SPECS = [spec("TRAILING_STOP", "protection"), spec("OCO", "protection"),
         spec("SLICER", "execution")]  # fmt: skip


def algo(code="TRAILING_STOP", instrument=FUT, state=AlgoState.WORKING, algo_id="A1", reason=""):
    return AlgoView(algo_id, code, "acc", "ayush", instrument, Side.SELL, 75, {}, state, 0, 0, 0,
                    (), reason, "", {}, T0, T0)  # fmt: skip


class Algos:
    def __init__(self):
        self.listeners, self.running = [], []

    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def specs(self):
        return SPECS

    def algos(self):
        return self.running

    def emit(self, kind, view, detail=""):
        for listener in self.listeners:
            listener(AlgoEvent(kind, T0, view, detail))


@dataclass
class Order:
    instrument_id: str
    side: Side
    order_type: OrderType
    is_working: bool = True

    @property
    def request(self):
        return self


@dataclass
class Gtt:
    instrument_id: str
    status: GttStatus = GttStatus.ACTIVE

    @property
    def request(self):
        return self


class Positions:
    def __init__(self):
        self.net = {}

    def net_qty(self, instrument_id):
        return self.net.get(instrument_id, 0)


class Recovery:
    def __init__(self, done=True):
        self.finished = asyncio.Event()
        if done:
            self.finished.set()


@dataclass
class Account:
    id: str


@dataclass
class Runtime:
    account: Account
    algos: Algos = field(default_factory=Algos)
    positions: Positions = field(default_factory=Positions)
    working: list = field(default_factory=list)
    gtt: object = None
    recovery: Recovery | None = None

    @property
    def orders(self):  # the order manager: runtime.orders.orders()
        return Gtts(self.working, "orders")


class Gtts:
    """A GTT book (`.list()`), or an order manager (`.orders()`): a list either way."""

    def __init__(self, items, method="list"):
        self.items = items
        setattr(self, method, lambda: self.items)


class FakePlatform:
    def __init__(self, *ids, lost=None):
        self.accounts = {i: Runtime(Account(i)) for i in ids}
        self.lost = lost or {}

    def lost_protection(self, account_id):
        return self.lost.pop(account_id, [])


def setup(*ids, lost=None):
    p = FakePlatform(*(ids or ("acc",)), lost=lost)
    alerts = Alerts(clock=lambda: T0)
    return p, alerts, ProtectionGuard(p, alerts)


def bands(alerts):
    return {a.key: a.text for a in alerts.active()}


def test_a_stopped_stop_on_an_open_position_raises_one_band_until_flat():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = 75
    rt.algos.emit("STOPPED", algo(state=AlgoState.STOPPED), "stopped by ayush")
    assert bands(alerts) == {"unprotected:acc:NFO:NIFTY26OCTFUT": (
        "UNPROTECTED acc: 75 NFO:NIFTY26OCTFUT (long) is open with no stop: its TRAILING_STOP "
        "(A1) stopped: stopped by ayush. Start a stop for it, or close it.")}  # fmt: skip
    guard.check()
    assert len(alerts.active()) == 1  # the same band, not a new one each check
    rt.positions.net[FUT] = -150  # the quantity changes: the band says so
    guard.check()
    assert "150 NFO:NIFTY26OCTFUT (short)" in bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"]
    told = []
    alerts.subscribe(told.append)
    rt.positions.net[FUT] = 0
    guard.check()
    assert bands(alerts) == {} and guard.flagged() == {}
    assert told[-1].level == "OK" and "the position is closed" in told[-1].text


def test_pause_fail_and_a_missed_exit_count_resume_and_a_new_stop_clear_it():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = 75
    rt.algos.emit("PAUSED", algo(state=AlgoState.PAUSED))
    assert "its TRAILING_STOP (A1) paused." in bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"]
    rt.algos.emit("RESUMED", algo())
    assert bands(alerts) == {}
    rt.algos.emit("FAILED", algo(state=AlgoState.FAILED, reason="the broker kept refusing"))
    assert "failed: the broker kept refusing" in bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"]
    rt.algos.emit("STARTED", algo("OCO", algo_id="A2"))  # a new stop on it
    assert bands(alerts) == {}
    rt.algos.emit("DONE", algo("OCO", state=AlgoState.DONE, algo_id="A2"))  # done, still open
    assert "its OCO (A2) done" in bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"]


def test_execution_algos_and_flat_positions_raise_nothing():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = 75
    rt.algos.emit("STOPPED", algo("SLICER", state=AlgoState.STOPPED))
    rt.positions.net[REL] = 0
    rt.algos.emit("STOPPED", algo(instrument=REL, state=AlgoState.STOPPED))
    assert bands(alerts) == {} and guard.flagged() == {}


def test_covered_again_by_a_working_stop_algo_a_stop_order_or_an_active_gtt():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = 75  # long: a SELL stop covers it

    def lose():
        rt.algos.emit("STOPPED", algo(state=AlgoState.STOPPED))
        return bool(bands(alerts))

    rt.algos.running = [algo("OCO", algo_id="A9")]
    assert not lose()
    rt.algos.running = [algo("SLICER", algo_id="A9"), algo("OCO", REL, algo_id="A8"),
                  algo("OCO", state=AlgoState.PAUSED, algo_id="A7")]  # fmt: skip
    assert lose()  # none of those protects this position
    rt.working = [Order(FUT, Side.BUY, OrderType.SL), Order(FUT, Side.SELL, OrderType.LIMIT),
                  Order(FUT, Side.SELL, OrderType.SL_M, is_working=False),
                  Order(REL, Side.SELL, OrderType.SL)]  # fmt: skip
    guard.check()
    assert bands(alerts)  # wrong side, not a stop, finished, other instrument
    rt.working.append(Order(FUT, Side.SELL, OrderType.SL_M))
    guard.check()
    assert bands(alerts) == {}
    rt.working.clear()
    assert lose()
    rt.gtt = Gtts([Gtt(FUT, GttStatus.TRIGGERED), Gtt(REL)])
    guard.check()
    assert bands(alerts)
    rt.gtt.items.append(Gtt(FUT))
    guard.check()
    assert bands(alerts) == {}


def test_after_a_restart_each_lost_stop_waits_for_recovery_then_shows():
    lost = {"acc": [LostProtection(FUT, "TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT (A1, ayush)")],
            "other": [LostProtection(REL, "OCO SELL 10 NSE:RELIANCE (A3, ayush)")]}  # fmt: skip
    p, alerts, guard = setup("acc", "other", lost=lost)
    rt = p.accounts["acc"]
    rt.recovery = Recovery(done=False)
    rt.positions.net[FUT] = 75  # not trusted yet: the rebuild hasn't finished
    p.accounts["other"].positions.net[REL] = 0  # recovered: flat, nothing to say
    guard.after_restart()
    assert bands(alerts) == {} and set(guard.flagged()) == {("acc", FUT)}
    rt.recovery.finished.set()
    guard.check()
    assert bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"].endswith(
        "with no stop: its stop (TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT (A1, ayush)) was "
        "running when the server stopped and is not running now. Start a stop for it, or close it."
    )


def test_an_account_added_later_is_watched_and_a_removed_one_forgotten():
    p, alerts, guard = setup()
    p.accounts["new"] = Runtime(Account("new"))
    guard.check()
    p.accounts["new"].positions.net[FUT] = 75
    p.accounts["new"].algos.emit("STOPPED", algo(state=AlgoState.STOPPED))
    assert "unprotected:new:NFO:NIFTY26OCTFUT" in bands(alerts)
    del p.accounts["new"]
    guard.check()
    assert bands(alerts) == {} and guard.flagged() == {}


async def test_the_scheduler_job_checks():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = 75
    rt.algos.emit("STOPPED", algo(state=AlgoState.STOPPED))
    rt.positions.net[FUT] = 0
    await guard.run_check()
    assert bands(alerts) == {}


def test_a_short_is_covered_only_by_a_buy_stop():
    p, alerts, guard = setup()
    rt = p.accounts["acc"]
    rt.positions.net[FUT] = -75
    rt.working = [Order(FUT, Side.SELL, OrderType.SL_M)]  # adds to the short: no protection
    rt.algos.emit("STOPPED", algo(state=AlgoState.STOPPED))
    assert "75 NFO:NIFTY26OCTFUT (short)" in bands(alerts)["unprotected:acc:NFO:NIFTY26OCTFUT"]
    rt.working.append(Order(FUT, Side.BUY, OrderType.SL_M))
    guard.check()
    assert bands(alerts) == {}
