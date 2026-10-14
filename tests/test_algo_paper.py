"""The algo engine running inside the platform against the paper broker (no Kite)."""

import asyncio

from mocktrade.algos.engine import AlgoState, AlgoType, BaseStrategy
from mocktrade.algos.spec import AlgoSpec, ParamKind, ParamSpec
from mocktrade.broker.paper import PaperBroker
from mocktrade.domain.accounts import BrokerAccount
from mocktrade.domain.orders import Side
from mocktrade.domain.ticks import DepthLevel, Tick
from mocktrade.marketdata.service import MarketData
from mocktrade.orders.model import ClientIds
from mocktrade.platform import Platform
from mocktrade.refdata.store import InstrumentStore
from mocktrade.risk.kill_switch import KillSwitch, stop_trading
from mocktrade.store.events import EventLog

FUT = "NFO:NIFTY26OCTFUT"


class TakeTheOffer(BaseStrategy):
    """Buys `lots` lots at the best ask every time it's asked (on_timer), until done."""

    def __init__(self, params):
        self.lots = params["lots"]

    def on_timer(self, ctx, now):
        tick = ctx.latest()
        if tick and tick.best_ask and not ctx.working():
            ctx.send(self.lots * ctx.instrument().lot_size, tick.best_ask.price_paise)


class JoinTheBid(BaseStrategy):
    """Rests one child at the best bid and waits."""

    def __init__(self, params):
        self.params = params

    def start(self, ctx):
        tick = ctx.latest()
        ctx.send(ctx.qty, tick.best_bid.price_paise)


SPEC = AlgoSpec(
    "TAKE", "Take", "buys at the offer", (ParamSpec("lots", "Lots", ParamKind.LOTS, "size"),)
)
REST = AlgoSpec("REST", "Rest", "joins the bid")


def book(ltp=2501000, ask_qty=75, volume=10_000):
    return Tick(
        FUT,
        ltp,
        volume=volume,
        bids=(DepthLevel(ltp, 150, 2),),
        asks=(DepthLevel(ltp + 10, ask_qty, 1),),
    )


async def until(condition, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


async def test_an_algo_works_a_parent_to_done_through_the_paper_broker(tmp_path, nifty_fut):
    store = InstrumentStore()
    store.add(nifty_fut)
    md = MarketData()
    md.on_tick(book())
    log = EventLog(tmp_path / "events.db")
    log.start()
    platform = Platform(store, md, KillSwitch("firm"), log)
    paper = PaperBroker(md, store, latency_s=0.0)
    paper.start()
    runtime = platform.add_account(
        BrokerAccount("acc-p", "paper", "P1", "ayush"),
        paper,
        ids=ClientIds("MT"),
        algo_types=(AlgoType(SPEC, TakeTheOffer), AlgoType(REST, JoinTheBid)),
    )
    running = asyncio.create_task(platform.run(reconcile=False))
    try:
        algo = runtime.algos.start(
            "TAKE", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=225, params={"lots": 1}
        )
        # each heartbeat buys one lot at the offer; the offer refreshes with each tick
        for n in range(1, 4):
            await until(lambda n=n: runtime.algos.get(algo.algo_id).filled_qty >= 75 * n)
            md.on_tick(book(volume=10_000 + 75 * n))
        await until(lambda: runtime.algos.get(algo.algo_id).state is AlgoState.DONE)
        view = runtime.algos.get(algo.algo_id)
        assert (view.filled_qty, view.avg_price_paise, len(view.children)) == (225, 2501010, 3)
        assert runtime.positions.net_qty(FUT) == 225  # fills reached the position book

        # a kill switch stops a working algo and pulls its resting child
        resting = runtime.algos.start(
            "REST", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=75, params={}
        )
        await until(lambda: runtime.orders.working() != [])
        await stop_trading(runtime.switch, [runtime.orders], "taking a break", by="ayush")
        await until(lambda: runtime.algos.get(resting.algo_id).state is AlgoState.STOPPED)
        assert runtime.algos.get(resting.algo_id).reason == "trading stopped: taking a break"
        assert runtime.orders.working() == []

        await log.flush()
        kinds = [e.kind for e in log.events(account_id="acc-p") if e.kind.startswith("ALGO_")]
        assert kinds[:1] == ["ALGO_STARTED"] and "ALGO_DONE" in kinds and "ALGO_STOPPED" in kinds
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await paper.aclose()
        await log.aclose()
