"""Rights re-checked when an action actually happens, not only when it was asked (#366).

    an admin removes a trader, disables them, or the account owner changes
      ─► their next new order or change          ─► refused, saying why
      ─► their closing orders and cancels         ─► still go (getting out stays possible)
      ─► their running algo's next child          ─► not sent: the algo stops, saying why
      ─► their GTT that could open a position     ─► deleted at the broker; a stop-loss stays

And limit races: many orders at once must not get past a limit that each alone would pass.
"""

import asyncio
from types import SimpleNamespace

from test_algo_paper import SPEC, TakeTheOffer, book
from test_gtt import FUT, LTP, World, single
from test_gtt_stop import long_75, opening_buy, running, stop, until

from mocktrade.algos.engine import AlgoState, AlgoType
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount, Role, User
from mocktrade.domain.gtt import GttStatus
from mocktrade.domain.orders import OrderRequest, OrderStatus, OrderType, Product, Side
from mocktrade.orders.model import ClientIds
from mocktrade.risk.access import trading_rights


class Users:
    """The two reads the check makes from the user store (server/users.py)."""

    def __init__(self):
        trader = frozenset({Role.TRADER})
        self.users = {"ayush": User("ayush", "Ayush", trader), "ravi": User("ravi", "Ravi", trader),
                      "risky": User("risky", "Risk", frozenset({Role.RISK}))}  # fmt: skip
        self.accounts = {"acc": BrokerAccount("acc", "paper", "P1", "ayush", frozenset({"ravi"}))}

    def get_user(self, user_id):
        return self.users.get(user_id)

    def get_account(self, account_id):
        return self.accounts.get(account_id)


def test_the_server_check_is_the_same_rule_as_the_screens():
    users = Users()
    check = trading_rights(users)
    assert check("ayush", "acc") is None  # the owner
    assert check("ravi", "acc") is None  # a trader the owner added
    assert "not a user" in check("ghost", "acc")
    assert "not a user" in check("ayush", "nope")
    assert "may no longer trade" in check("risky", "acc")  # risk watches, never trades

    users.accounts["acc"] = BrokerAccount("acc", "paper", "P1", "ayush")  # ravi removed
    assert check("ravi", "acc") == "ravi may no longer trade acc"  # removed: read fresh
    users.users["ayush"] = User("ayush", "Ayush", frozenset({Role.TRADER}), active=False)
    assert check("ayush", "acc") == "ayush is disabled"
    users.users["ayush"] = User("ayush", "Ayush", frozenset({Role.TRADER}))
    users.accounts["acc"] = BrokerAccount("acc", "paper", "P1", "ayush", active=False)
    assert check("ayush", "acc") is None  # turned off: the risk gate allows closing only


def revocable(w):
    """Ayush's rights, switchable by the test like an admin's screen."""
    gone = {}
    w.platform.access = lambda user_id, _account: gone.get(user_id)
    return gone


def buy(qty=75, **kw):
    return OrderRequest("", "acc", "ayush", FUT, Side.BUY, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=kw.pop("price", LTP + 10), **kw)  # fmt: skip


def sell(qty=75, **kw):
    return OrderRequest("", "acc", "ayush", FUT, Side.SELL, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=LTP - 10, **kw)  # fmt: skip


async def test_new_orders_are_refused_but_closes_and_cancels_still_go(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    gone = revocable(w)
    run = await running(w)
    try:
        await long_75(w)
        resting = await w.rt.orders.submit(buy(price=LTP - 5000))  # far below: stays working
        assert resting.status is not OrderStatus.REJECTED
        gone["ayush"] = "ayush may no longer trade acc"

        refused = await w.rt.orders.submit(buy())
        assert refused.status is OrderStatus.REJECTED
        assert refused.reason == "ayush may no longer trade acc"
        try:
            await w.rt.orders.modify(resting.client_order_id, qty=150)  # adds risk: refused
            raise AssertionError("the change went through")
        except Exception as exc:
            assert "may no longer trade" in str(exc)

        cancelled = await w.rt.orders.cancel(resting.client_order_id)  # always allowed
        await until(lambda: w.rt.orders.get(cancelled.client_order_id).status
                    is OrderStatus.CANCELLED)  # fmt: skip
        closed = await w.rt.orders.submit(sell(reduce_only=True))  # getting out stays possible
        assert closed.status is not OrderStatus.REJECTED
        await until(lambda: w.rt.positions.net_qty(FUT) == 0)

        del gone["ayush"]  # given back: works again at once, no restart
        assert (await w.rt.orders.submit(buy())).status is not OrderStatus.REJECTED
    finally:
        await stop(run, w)


async def test_a_running_algo_stops_before_its_next_child(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    gone = revocable(w)
    paper = PaperBroker(w.md, w.store, latency_s=0.0)  # its own: one broker per account
    paper.start()
    algos = w.platform.add_account(
        BrokerAccount("acc2", "paper", "P2", "ayush"), paper, ids=ClientIds("MU"),
        algo_types=(AlgoType(SPEC, TakeTheOffer),),
    )  # fmt: skip
    w.md.on_tick(book())
    run = await running(w)
    try:
        algo = algos.algos.start("TAKE", user_id="ayush", instrument_id=FUT, side=Side.BUY,
                                 qty=225, params={"lots": 1})  # fmt: skip
        await until(lambda: algos.algos.get(algo.algo_id).filled_qty >= 75)
        gone["ayush"] = "ayush is disabled"
        w.md.on_tick(book(volume=10_075))  # a fresh offer: it would buy the next lot now
        await until(lambda: algos.algos.get(algo.algo_id).state is AlgoState.STOPPED)
        view = algos.algos.get(algo.algo_id)
        assert view.reason == "ayush is disabled"
        assert (view.filled_qty, len(view.children)) == (75, 1)  # nothing more was sent
    finally:
        await stop(run, w)
        await paper.aclose()


async def test_gtts_that_could_open_are_deleted_and_stop_losses_kept(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut)
    gone = revocable(w)
    run = await running(w)
    try:
        await long_75(w)
        protect = await w.rt.gtt.place(single())  # the long's stop-loss
        opener = await w.rt.gtt.place(opening_buy())
        await asyncio.sleep(0.1)
        assert w.rt.gtt.get(opener.gtt_id).status is GttStatus.ACTIVE  # rights intact: kept

        gone["ayush"] = "ayush may no longer trade acc"
        await until(lambda: w.rt.gtt.get(opener.gtt_id).status is GttStatus.DELETED)
        assert w.rt.gtt.get(protect.gtt_id).status is GttStatus.ACTIVE  # still protects
        theirs = {g.gtt_id: g.status for g in await w.broker.gtts()}
        assert theirs[opener.gtt_id] is not GttStatus.ACTIVE  # gone at the broker too
        assert w.rt.gtt.get(opener.gtt_id).reason == (
            f"GTT {opener.gtt_id} deleted: its owner may no longer trade this account "
            "(ayush may no longer trade acc) and it could open a position"
        )
    finally:
        await stop(run, w)


async def test_many_orders_at_once_cannot_pass_the_position_limit(tmp_path, nifty_fut):
    """Each order alone fits; together they would not. The gate counts orders on their way,
    so at most the limit gets through, however they interleave."""
    w = World(tmp_path, nifty_fut, max_position_lots=3, duplicate_window_s=0)
    run = await running(w)
    try:
        views = await asyncio.gather(*(w.rt.orders.submit(buy()) for _ in range(10)))
        accepted = [v for v in views if v.status is not OrderStatus.REJECTED]
        assert len(accepted) == 3
        await until(lambda: w.rt.positions.net_qty(FUT) == 225)
        await asyncio.sleep(0.1)
        assert w.rt.positions.net_qty(FUT) == 225  # 3 lots, never more
    finally:
        await stop(run, w)


async def test_many_orders_at_once_cannot_pass_the_open_orders_limit(tmp_path, nifty_fut):
    w = World(tmp_path, nifty_fut, max_open_orders=4, max_position_lots=50, duplicate_window_s=0)
    run = await running(w)
    try:
        far = [buy(price=LTP - 5000 - n * 5) for n in range(12)]  # far below: all stay working
        views = await asyncio.gather(*(w.rt.orders.submit(r) for r in far))
        assert sum(v.status is not OrderStatus.REJECTED for v in views) == 4
        assert len(w.rt.orders.working()) == 4
    finally:
        await stop(run, w)


def test_the_platform_check_is_read_at_each_call():
    """Set after the accounts were added (as the server does): still used."""
    from mocktrade.risk.access import AccessGate

    holder = SimpleNamespace(access=lambda _u, _a: None)
    gate = AccessGate("acc", lambda: holder.access)
    holder.access = lambda u, a: f"{u} out of {a}"
    asked = buy()
    assert asyncio.run(gate.admit_new(asked)) == "ayush out of acc"
    assert asyncio.run(gate.admit_new(sell(reduce_only=True))) is None
    assert asyncio.run(gate.admit_cancel(object())) is None


def test_the_real_server_checks_its_user_store(tmp_path):
    """serve.build hands the platform the user store's check, not the do-nothing default."""
    from test_serve import env

    from mocktrade.tools.serve import ServeSettings, build

    built = build(ServeSettings.from_env(env(MOCKTRADE_DATA=str(tmp_path / "data"))))
    try:
        assert built.platform.access("ghost", "acc-1") == "ghost is not a user of acc-1"
    finally:
        if getattr(built, "fence", None) is not None:
            built.fence.release()
