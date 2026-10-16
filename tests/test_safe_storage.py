"""#269: what reaches a file name can never reach outside its folder, and an import is checked
whole before anything is written.

The audit's reproduction: `CandleStore.put("../outside", day, [])` under `<tmp>/candles` wrote
`<tmp>/outside/<day>.json`. Every case below runs inside pytest's temporary folder.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mocktrade.domain.accounts import BrokerAccount, Role
from mocktrade.research.backtest import BacktestError, CandleStore
from mocktrade.server.app import ServerSettings, create_app
from mocktrade.server.security import SecretBox
from mocktrade.server.users import UserStore

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 16)
T0 = datetime(2026, 10, 16, 9, 15, tzinfo=IST)
GOOD = {"time": T0.isoformat(), "open": 100, "high": 110, "low": 90, "close": 105, "volume": 7}


def everything_under(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


@pytest.mark.parametrize(
    "bad",
    ["../outside", "NSE:../../outside", "/tmp/x", "NSE:/etc", "NSE:a/b", "NSE:a\\b", "nse:x",
     "NSE:", ":X", "NSE:..", "NSE:.hidden", "NSE:x\x00y", "NSE:" + "A" * 61, "NSE:A..B"],
)  # fmt: skip
def test_an_id_with_path_characters_is_refused_and_nothing_leaves_the_root(tmp_path, bad):
    store = CandleStore(tmp_path / "candles")
    with pytest.raises(BacktestError, match="not an instrument id"):
        store.put(bad, DAY, [GOOD])
    assert everything_under(tmp_path) == []  # not even the root was made
    assert store.rows(bad, DAY) is None and store.days(bad) == []  # reads: simply nothing


@pytest.mark.parametrize("ok", ["NSE:M&M", "NSE:NIFTY 50", "NSE:BAJAJ-AUTO", "NFO:NIFTY26OCTFUT",
                                "BSE:SBIN.X", "NSE:A_B"])  # fmt: skip
def test_real_symbols_still_work(tmp_path, ok):
    store = CandleStore(tmp_path / "candles")
    assert store.put(ok, DAY, [GOOD]) == 1
    assert store.rows(ok, DAY) == [GOOD] and store.days(ok) == ["2026-10-16"]
    (folder,) = (tmp_path / "candles").iterdir()
    assert folder.name == ok.replace(":", "_")  # the same layout as before: old data reads


def test_a_bad_import_writes_nothing_and_keeps_the_day_there(tmp_path):
    store = CandleStore(tmp_path / "candles")
    store.put("NSE:X", DAY, [GOOD])
    later = {**GOOD, "time": (T0 + timedelta(minutes=1)).isoformat()}
    for bad, words in [
        ([GOOD, GOOD], "a second candle at"),
        ([{**GOOD, "volume": -1}], "volume can't be negative"),
        ([{**GOOD, "open": "100"}], "needs time"),
        ([{**GOOD, "close": 105.5}], "needs time"),
        ([{**GOOD, "high": True}], "needs time"),
        ([{**GOOD, "oi": 5}], "unknown field oi"),
        ([later, {**GOOD, "low": 120}], "row 2: low ≤ open"),
    ]:
        with pytest.raises(BacktestError, match=words):
            store.put("NSE:X", DAY, bad)
        assert store.rows("NSE:X", DAY) == [GOOD]  # the earlier day is untouched
    assert everything_under(tmp_path / "candles") == ["NSE_X", "NSE_X/2026-10-16.json"]  # no .part
    assert store.put("NSE:X", DAY, [later, GOOD]) == 2  # replaced whole, in time order
    assert [r["time"] for r in store.rows("NSE:X", DAY)] == [GOOD["time"], later["time"]]
    (tmp_path / "candles" / "NSE_X" / "notes.json").write_text("{}")
    assert store.days("NSE:X") == ["2026-10-16"]  # only day files count as days


def test_account_ids_are_safe_file_names(tmp_path):
    store = UserStore(tmp_path / "u.db", SecretBox(SecretBox.new_key()))
    store.create_user("ana", "Ana", "a long enough password", {Role.TRADER})
    for bad in ("../x", "acc/x", "acc x", "acc.x", "ácc"):
        with pytest.raises(ValueError, match="account id must be letters, digits or dashes"):
            store.add_account(BrokerAccount(bad, "paper", "P1", "ana"))
    store.add_account(BrokerAccount("acc-ana-1", "paper", "P1", "ana"))


async def test_the_import_api_wants_a_known_contract_and_records_who(tmp_path, nifty_fut):
    from test_server import World

    w = World(tmp_path, nifty_fut)
    # the World's app has no candle store: build one that has
    w.app = create_app(w.platform, w.users, ServerSettings(origin="https://trade.example.in"),
                       candles=CandleStore(tmp_path / "candles"),
                       monotonic=w.clock.mono)  # fmt: skip
    c = await w.logged_in()
    path = "/api/research/candles"
    unknown = await c.post(path, json={"instrument_id": "NSE:../../x", "day": "2026-10-16",
                                       "rows": [GOOD]})  # fmt: skip
    assert unknown.status_code == 422 and "unknown instrument" in unknown.json()["detail"]
    ok = await c.post(path, json={"instrument_id": nifty_fut.id, "day": "2026-10-16",
                                  "rows": [GOOD]})  # fmt: skip
    assert ok.status_code == 201
    await w.log.flush()  # the audit trail is written in the background
    (note,) = w.log.notes("-", ["CANDLES_IMPORTED"])
    assert note.extra == {"by": "ayush", "instrument_id": nifty_fut.id, "day": "2026-10-16",
                          "rows": 1}  # fmt: skip
    folder = "NFO_NIFTY26OCTFUT"
    assert everything_under(tmp_path / "candles") == [folder, f"{folder}/2026-10-16.json"]
    await c.aclose()
    w.log.close()


def test_a_link_planted_in_the_folder_is_not_followed_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "candles"
    root.mkdir()
    (root / "NSE_X").symlink_to(outside, target_is_directory=True)
    store = CandleStore(root)
    with pytest.raises(BacktestError, match="not an instrument id"):
        store.put("NSE:X", DAY, [GOOD])
    assert list(outside.iterdir()) == [] and store.rows("NSE:X", DAY) is None
