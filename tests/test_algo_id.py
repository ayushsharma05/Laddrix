"""The exchange algo id on algo orders (#291).

SEBI's retail algo framework (in force from 1 April 2026): above 10 orders a second per
exchange, an algo must be registered with the exchange, and its orders carry the exchange's
algo id. Before, the slot existed but nothing set it, nothing showed whether an algo had one,
and nothing stopped an unregistered algo from running faster than the line."""

import asyncio

import pytest
from test_algo_engine import FUT, SPEC, Scripted, start
from test_algo_engine import world as _world  # noqa: F401  (the fixture)

from laddrix.algos.builtin import BUILTIN
from laddrix.algos.engine import (
    AlgoEngine,
    AlgoError,
    AlgoType,
    algo_id_note,
    parse_algo_ids,
    with_exchange_ids,
)
from laddrix.domain.orders import Side
from laddrix.risk.throttle import DEFAULT_RULES, OrderThrottle, RateRule


@pytest.fixture
def world(_world):  # noqa: F811
    return _world


def go(engine: AlgoEngine):
    return engine.start("TEST", user_id="ayush", instrument_id=FUT, side=Side.BUY, qty=150,
                        params={"slices": 2, "lots": 1})  # fmt: skip


def engine_with(w, kind: AlgoType, per_second: int) -> AlgoEngine:
    return AlgoEngine("acc-1", w.om, w.md, w.store.get, [kind], clock=w.engine.clock,
                      orders_per_second=lambda: per_second)  # fmt: skip


def test_ids_come_from_the_settings_per_algo_code():
    assert parse_algo_ids("slicer=AB123, TWAP = CD456,") == {"SLICER": "AB123", "TWAP": "CD456"}
    with pytest.raises(ValueError, match="'TWAP' is not CODE=ID"):
        parse_algo_ids("TWAP")
    types = with_exchange_ids(BUILTIN, {"SLICER": "AB123"})
    by_code = {t.spec.code: t.exchange_algo_id for t in types}
    assert by_code["SLICER"] == "AB123" and by_code["TWAP"] is None


async def test_an_algos_orders_carry_its_exchange_id(world):
    w = world
    w.engine = engine_with(w, AlgoType(SPEC, Scripted, exchange_algo_id="NSE-ALGO-77"), 8)
    start(w, slices=2)
    await asyncio.sleep(0.05)
    assert w.broker.placed and all(r.algo_id == "NSE-ALGO-77" for r in w.broker.placed)
    assert all(r.parent_id for r in w.broker.placed)  # and its own algo, for the audit trail


async def test_without_an_id_an_algo_runs_only_at_or_below_ten_a_second(world):
    w = world
    unregistered = AlgoType(SPEC, Scripted)
    assert go(engine_with(w, unregistered, 10))  # at the line: fine
    with pytest.raises(AlgoError, match="TEST has no exchange algo id, and this account may "
                                        "send 20 orders a second"):  # fmt: skip
        go(engine_with(w, unregistered, 20))
    assert go(engine_with(w, AlgoType(SPEC, Scripted, exchange_algo_id="NSE-ALGO-77"), 20))


def test_the_throttle_says_its_per_second_limit():
    assert OrderThrottle().per_second == 8  # below SEBI's line: no id needed
    fast = (RateRule("per second", 1.0, limit=20, cancel_limit=25), *DEFAULT_RULES[1:])
    assert OrderThrottle(fast).per_second == 20


def test_the_screens_are_told_whether_an_algo_has_one():
    assert "no exchange algo id: allowed while orders stay at or below 10 a second" in (
        algo_id_note(AlgoType(SPEC, Scripted))
    )
    assert algo_id_note(AlgoType(SPEC, Scripted, exchange_algo_id="X1")).startswith(
        "exchange algo id X1: its orders carry it"
    )


def test_the_server_refuses_ids_for_algos_it_doesnt_have():
    from test_serve import env

    from laddrix.config import ConfigError
    from laddrix.tools.serve import ServeSettings

    s = ServeSettings.from_env(env(LADDRIX_ALGO_IDS="SLICER=AB123"))
    assert s.algo_ids == (("SLICER", "AB123"),)
    with pytest.raises(ConfigError, match="no algo called NOPE"):
        ServeSettings.from_env(env(LADDRIX_ALGO_IDS="NOPE=1"))


async def test_algo_specs_say_it_over_the_api(tmp_path, nifty_fut):
    from test_server import World

    srv = World(tmp_path, nifty_fut)
    c = await srv.logged_in()
    specs = (await c.get("/api/accounts/acc-ayush/algo-specs")).json()
    assert specs and all("exchange_algo_note" in s and "exchange_algo_id" in s for s in specs)
    await c.aclose()
    srv.log.close()
