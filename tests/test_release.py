"""Releases (V1.6, #289): one version everywhere, shown by /api/health, and no deploy in market
hours (09:00–15:45 India time on trading days): deploy/window.sh, run here as on the server."""

import json
import os
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import laddrix
from laddrix.version import VERSION

ROOT = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")


def ist(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


def test_one_version_everywhere():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-rc\.\d+)?", VERSION)
    pep440 = re.sub(r"-rc\.(\d+)$", r"rc\1", VERSION)
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == pep440
    for app in ("ui", "ui/desktop"):  # the screens and the desktop app around them
        assert json.loads((ROOT / app / "package.json").read_text())["version"] == VERSION
        lock = json.loads((ROOT / app / "package-lock.json").read_text())
        assert lock["version"] == lock["packages"][""]["version"] == VERSION
    assert laddrix.__version__ == VERSION
    notes = ROOT / "docs/releases" / f"v{VERSION.split('-')[0]}.md"
    assert notes.exists(), "every version has release notes"
    assert f"## {VERSION}" in (ROOT / "CHANGELOG.md").read_text()


def window(when: datetime, holidays: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "DEPLOY_NOW": str(int(when.timestamp())), "LADDRIX_HOLIDAYS": holidays}
    return subprocess.run([ROOT / "deploy/window.sh"], env=env, capture_output=True, text=True)


@pytest.mark.parametrize(
    "when, inside",
    [
        ("2026-10-16 08:59", False),  # a Friday, before the pre-open
        ("2026-10-16 09:00", True),
        ("2026-10-16 12:00", True),
        ("2026-10-16 15:44", True),
        ("2026-10-16 15:45", False),
        ("2026-10-17 11:00", False),  # Saturday
        ("2026-10-18 11:00", False),  # Sunday
        ("2026-10-19 09:30", True),  # Monday
        ("2026-10-21 11:00", False),  # a holiday (below)
    ],
)
def test_the_market_hours_window(when, inside):
    r = window(ist(when), "2026-10-21, 2026-11-10")
    assert r.returncode == (3 if inside else 0), r.stderr
    if inside:
        assert "inside market hours (09:00-15:45 on trading days)" in r.stderr
    else:
        assert "a deploy may go ahead" in r.stdout


def test_the_window_is_india_time_whatever_the_server_clock():
    assert window(datetime(2026, 10, 16, 5, 0, tzinfo=UTC)).returncode == 3  # 10:30 in India
    assert window(datetime(2026, 10, 16, 3, 0, tzinfo=UTC)).returncode == 0  # 08:30 in India
    r = window(datetime(2026, 10, 16, 4, 30, tzinfo=UTC))
    assert "Fri 16 Oct 10:00 India time" in r.stderr


@pytest.mark.parametrize("bad", ["2026-13-01", "21-10-2026", "soon"])
def test_a_bad_holiday_list_is_an_error_not_skipped(bad):
    r = window(ist("2026-10-16 18:00"), f"2026-10-21,{bad}")
    assert r.returncode == 2 and "not a list of dates" in r.stderr


async def test_health_says_the_version(tmp_path, nifty_fut):
    from test_server import World

    w = World(tmp_path, nifty_fut)
    c = w.client()
    body = (await c.get("/api/health")).json()
    assert body == {"status": "ok", "version": VERSION, "build": "dev"}
    await c.aclose()
    w.log.close()
