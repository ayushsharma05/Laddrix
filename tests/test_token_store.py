"""Today's Kite token kept privately on disk until 6 AM (broker/kite/token_store.py)."""

import os
import stat
from datetime import datetime

from laddrix.broker.kite import token_store
from laddrix.broker.kite.session import KiteSession

IST = token_store.IST


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def test_token_made_after_6am_expires_next_morning():
    assert token_store.expires_at(at(14, 9, 5)) == at(15, 6)


def test_token_made_before_6am_expires_the_same_morning():
    assert token_store.expires_at(at(14, 5, 30)) == at(14, 6)


def test_saved_token_is_private_and_loads_until_expiry(tmp_path):
    path = tmp_path / "data" / "kite_token.json"
    token_store.save(path, KiteSession("AB1234", "ACC"), at(14, 9))
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token_store.load(path, at(15, 5, 59)).access_token == "ACC"
    assert token_store.load(path, at(15, 6)) is None


def test_missing_file_means_no_session(tmp_path):
    assert token_store.load(tmp_path / "none.json", at(14, 9)) is None
