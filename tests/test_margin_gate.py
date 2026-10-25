"""The pre-trade margin check (K10.4, #216): refuse what the funds can't carry, never block
an exit, and let orders through (with a warning) when the broker or the funds can't answer."""

import asyncio
from fractions import Fraction

import pytest

from laddrix.broker.base import BasketMargin, BrokerError
from laddrix.domain.orders import OrderRequest, OrderType, Product, Side
from laddrix.risk.margin import MarginGate

FUT = "NFO:NIFTY26OCTFUT"


def order(side=Side.BUY, qty=75, price=2501000):
    return OrderRequest("MT1", "acc", "ayush", FUT, side, qty, OrderType.LIMIT, Product.NRML,
                        price_paise=price)  # fmt: skip


class Quoter:
    def __init__(self, final=22507920, fail=None, slow=False):
        self.final, self.fail, self.slow, self.asked = final, fail, slow, []

    async def basket_margin(self, requests):
        self.asked.append(requests)
        if self.slow:
            await asyncio.sleep(5)
        if self.fail:
            raise self.fail
        # initial (the order alone) is more than final (with the positions' hedge): the gate
        # must use final, what the broker actually blocks
        return BasketMargin(self.final + 10_000_000, self.final)


def gate(quoter, available=30_000_000, net=0, **kw):
    unchecked = []

    async def funds():
        if isinstance(available, Exception):
            raise available
        return available

    g = MarginGate(quoter, funds, lambda _i: net, on_unchecked=lambda r, why: unchecked.append(why),
                   **kw)  # fmt: skip
    return g, unchecked


async def test_enough_margin_goes_and_too_little_is_refused_with_both_numbers():
    g, _ = gate(Quoter(), available=30_000_000)
    assert await g.admit_new(order()) is None
    g, _ = gate(Quoter(), available=15_000_000)
    assert await g.admit_new(order()) == (
        "not enough margin: this order needs ₹2,25,079.20, ₹1,50,000.00 is available"
    )


async def test_an_exit_is_never_asked_about_and_always_goes():
    q = Quoter()
    g, _ = gate(q, available=0, net=150)
    assert await g.admit_new(order(Side.SELL, 75)) is None
    assert await g.admit_new(order(Side.SELL, 150)) is None
    assert q.asked == []  # no broker round trip for getting out
    short, _ = gate(q, available=0, net=-75)
    assert await short.admit_new(order(Side.BUY, 75)) is None
    # more than the position turns it around: that part opens a new position, so it's checked
    assert "not enough margin" in await g.admit_new(order(Side.SELL, 225))
    assert "not enough margin" in await g.admit_new(order(Side.BUY, 75))  # adds to a long


@pytest.mark.parametrize(
    "quoter, available, why",
    [
        (Quoter(fail=BrokerError("Kite /margins/basket: down")), 0,
         "the broker couldn't quote it: Kite /margins/basket: down"),
        (Quoter(fail=RuntimeError("boom")), 0, "no margin quote from the broker"),
        (Quoter(slow=True), 0, "no margin quote within 0.05 s"),
        (Quoter(), None, "available funds unknown"),
        (Quoter(), RuntimeError("funds down"), "available funds unknown"),
        (object(), 0, "this broker doesn't quote margins"),
    ],
)  # fmt: skip
async def test_no_answer_fails_open_with_a_warning(quoter, available, why):
    g, unchecked = gate(quoter, available=available, timeout_s=0.05)
    assert await g.admit_new(order()) is None  # never blocked by an outage
    assert unchecked == [why]


async def test_modify_checks_only_an_increase_and_cancel_always_goes():
    from datetime import UTC, datetime

    from laddrix.orders.model import OrderStatus, OrderView, Pending

    now = datetime.now(UTC)
    q = Quoter(final=10_000_000)
    g, _ = gate(q, available=15_000_000)
    working = OrderView(order(qty=75), "B1", OrderStatus.OPEN, 0, 0, "", Pending.NONE, now, now)
    assert await g.admit_modify(working, order(qty=75, price=2500000)) is None
    assert q.asked == []  # a price change needs no new margin
    assert await g.admit_modify(working, order(qty=150)) is None
    assert q.asked[-1][0].qty == 75  # only the increase is quoted
    q.final = 20_000_000
    assert "not enough margin" in await g.admit_modify(working, order(qty=150))
    assert await g.admit_cancel(working) is None


# --- paper: an estimate from PaperMargins, and the gate on a real account ------------------------


async def test_paper_quotes_the_extra_margin_and_an_exit_adds_nothing(nifty_fut):
    from test_portfolio import Paper

    p = Paper(nifty_fut)
    q = await p.paper.basket_margin([order()])
    assert q.final_paise == q.initial_paise == round(Fraction(75 * 2501000 * 12, 100))
    await p.buy(FUT, 75, 2501010, Product.NRML)
    out = await p.paper.basket_margin([order(Side.SELL, 75)])
    assert out.final_paise == 0 and out.initial_paise > 0  # on its own it would open a short
    nope = OrderRequest("X", "acc", "a", "NFO:NOPE", Side.BUY, 75, OrderType.LIMIT, Product.NRML,
                        price_paise=1)  # fmt: skip
    with pytest.raises(BrokerError, match="unknown instrument"):
        await p.paper.basket_margin([nope])
    await p.stop()


async def test_a_paper_account_with_the_check_on(nifty_fut):
    from test_portfolio import Paper

    from laddrix.domain.accounts import BrokerAccount
    from laddrix.orders.model import ClientIds, OrderStatus

    p = Paper(nifty_fut)
    p.platform.accounts.clear()
    p.paper.starting_paise = 3_00_000_00  # ₹3 lakh: room for one lot (about ₹2.25 lakh)
    rt = p.platform.add_account(
        BrokerAccount("acc", "paper", "P1", "ayush"), p.paper, ids=ClientIds("MC"),
        limits=p.rt.risk.limits, margin_check=True,
    )  # fmt: skip
    p.rt = rt
    await p.buy(FUT, 75, 2501010, Product.NRML)
    await rt.portfolio.refresh("funds")  # what a fill does a moment later
    second = await rt.orders.submit(order(qty=75, price=2501010))
    assert second.status is OrderStatus.REJECTED
    assert second.reason.startswith("not enough margin: this order needs ₹2,25,0")
    await p.buy(FUT, 75, 2500990, Product.NRML, side=Side.SELL)  # the exit always goes
    assert rt.positions.net_qty(FUT) == 0
    await p.stop()


async def test_the_margin_api_and_an_unchecked_order_is_audited(tmp_path, nifty_fut):
    from test_server import World as ServerWorld

    w = ServerWorld(tmp_path, nifty_fut)  # fake brokers: no margin quotes, no funds
    c = await w.logged_in()
    body = {"instrument_id": FUT, "side": "BUY", "qty": 75, "price_paise": 2501000,
            "product": "NRML"}  # fmt: skip
    r = await c.post("/api/accounts/acc-ayush/margin", json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"exit": False, "required_paise": None, "available_paise": None,
                        "enough": None, "note": "this broker doesn't quote margins",
                        "checked_before_sending": False}  # fmt: skip
    rt = w.platform.account("acc-ayush")
    await rt.margin.admit_new(order())  # what the gate does when it's on
    await w.log.flush()
    notes = [e.detail for e in w.log.events(account_id="acc-ayush") if e.kind == "MARGIN_UNCHECKED"]
    assert notes == ["BUY 75 NFO:NIFTY26OCTFUT sent without a margin check: "
                     "this broker doesn't quote margins"]  # fmt: skip
    await c.aclose()
    w.log.close()
