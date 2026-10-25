"""The server entry point: settings from the environment, and the parts it assembles."""

import httpx
import pytest

from laddrix.config import ConfigError
from laddrix.domain.accounts import BrokerAccount, Role
from laddrix.server.security import SecretBox
from laddrix.server.users import UserStore
from laddrix.tools.serve import ServeSettings, build, main

KEY = SecretBox.new_key()


def env(**extra):
    return {"LADDRIX_SECRET_KEY": KEY, "LADDRIX_ORIGIN": "https://trade.example.in", **extra}


def test_good_settings_and_their_defaults():
    s = ServeSettings.from_env(env(LADDRIX_ORIGIN="https://trade.example.in/"))
    assert s.origin == "https://trade.example.in"  # a trailing slash would break origin checks
    assert (s.host, s.port, s.prices, s.trusted_proxies) == ("127.0.0.1", 8000, "sim", "")
    assert s.cookie_secure
    assert s.paper_funds_paise == 10_00_000_00  # ₹10 lakh of paper money
    rich = ServeSettings.from_env(env(LADDRIX_PAPER_FUNDS="2500000"))
    assert rich.paper_funds_paise == 25_00_000_00
    with pytest.raises(ConfigError, match="LADDRIX_PAPER_FUNDS must be whole rupees"):
        ServeSettings.from_env(env(LADDRIX_PAPER_FUNDS="1e6"))


def test_a_charge_schedule_file_is_checked_at_start(tmp_path):
    import json

    from laddrix.charges.schedules import KITE_STANDARD, schedule_to_dict

    good = tmp_path / "charges.json"
    good.write_text(json.dumps(schedule_to_dict(KITE_STANDARD)))
    assert ServeSettings.from_env(env(LADDRIX_CHARGES=str(good))).charges_file == good
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "checked_on": "2026-10-15", "segments": {}}))
    with pytest.raises(ConfigError, match="LADDRIX_CHARGES: charge schedule: no rates for"):
        ServeSettings.from_env(env(LADDRIX_CHARGES=str(bad)))
    with pytest.raises(ConfigError, match="LADDRIX_CHARGES"):
        ServeSettings.from_env(env(LADDRIX_CHARGES=str(tmp_path / "missing.json")))


def test_plain_http_only_on_this_machine():
    local = ServeSettings.from_env(env(LADDRIX_ORIGIN="http://localhost:8000"))
    assert not local.cookie_secure  # browsers drop Secure cookies over http
    with pytest.raises(ConfigError, match="must be https://"):
        ServeSettings.from_env(env(LADDRIX_ORIGIN="http://trade.example.in"))


def test_every_problem_is_named_at_once():
    with pytest.raises(ConfigError) as caught:
        ServeSettings.from_env({"LADDRIX_PORT": "eighty", "LADDRIX_PRICES": "live"})
    text = str(caught.value)
    for name in ("LADDRIX_SECRET_KEY", "LADDRIX_ORIGIN", "LADDRIX_PORT", "LADDRIX_PRICES"):
        assert name in text


def test_kite_mode_needs_a_kite_app():
    with pytest.raises(ConfigError, match="needs KITE_API_KEY"):
        ServeSettings.from_env(env(LADDRIX_PRICES="kite"))
    s = ServeSettings.from_env(
        env(
            LADDRIX_PRICES="kite",
            KITE_API_KEY="shared",
            KITE_API_SECRET="s1",
            KITE_API_KEY_ACC_AYUSH="own",
            KITE_API_SECRET_ACC_AYUSH="s2",
        )
    )
    assert s.kite_app(BrokerAccount("acc-ayush", "kite", "AB1234", "ayush")).api_key == "own"
    other = s.kite_app(BrokerAccount("acc-2", "kite", "CD5678", "x"))
    assert (other.api_key, other.api_secret) == ("shared", "s1")


def test_a_kite_account_without_an_app_stops_the_start(tmp_path):
    users = UserStore(tmp_path / "users.db", SecretBox(KEY))
    users.create_user("ayush", "Ayush", "a-long-password-1", {Role.TRADER})
    users.add_account(BrokerAccount("acc-kite", "kite", "AB1234", "ayush"))
    s = ServeSettings.from_env(
        env(LADDRIX_DATA=str(tmp_path), LADDRIX_PRICES="kite", KITE_API_KEY_ACC_OTHER="k")
    )
    with pytest.raises(ConfigError, match="acc-kite: no Kite app"):
        build(s)


@pytest.mark.parametrize(
    "bad", ["trade.example.in", "https://trade.example.in/app", "ftp://trade.example.in"]
)
def test_an_origin_is_scheme_and_host_only(bad):
    with pytest.raises(ConfigError, match="scheme://host"):
        ServeSettings.from_env(env(LADDRIX_ORIGIN=bad))


def test_a_wrong_secret_key_is_refused():
    with pytest.raises(ConfigError, match="not a valid key"):
        ServeSettings.from_env(env(LADDRIX_SECRET_KEY="short"))


def test_check_exits_0_or_2(capsys):
    assert main(["--check"], env()) == 0
    assert "settings OK" in capsys.readouterr().out
    assert main(["--check"], {}) == 2
    assert "LADDRIX_SECRET_KEY" in capsys.readouterr().err


async def test_paper_accounts_run_and_others_are_listed_not_run(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(KEY))
    users.create_user("ayush", "Ayush", "a-long-password-1", {Role.ADMIN, Role.TRADER})
    users.add_account(BrokerAccount("acc-paper", "paper", "P1", "ayush"))
    users.add_account(BrokerAccount("acc-kite", "kite", "AB1234", "ayush"))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>Laddrix</title>")
    built = build(ServeSettings.from_env(env(LADDRIX_DATA=str(data), LADDRIX_UI=str(ui))))
    try:
        assert built.running == ["acc-paper"]
        assert built.platform.account("acc-kite") is None  # never traded on paper by surprise
        # a restart never starts trading from an empty state: rebuilt first (#206)
        assert built.platform.account("acc-paper").recovery.state == "recovering"
        assert "LADDRIX_PRICES=kite" in built.not_running["acc-kite"]
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).json()["status"] == "ok"
            page = await client.get("/")
            assert page.status_code == 200 and "Laddrix" in page.text
    finally:
        await built.close()


async def test_without_built_screens_the_api_still_serves(tmp_path):
    built = build(
        ServeSettings.from_env(
            env(LADDRIX_DATA=str(tmp_path / "d"), LADDRIX_UI=str(tmp_path / "none"))
        )
    )
    try:
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).status_code == 200
        assert built.running == []
    finally:
        await built.close()


def test_sigterm_stops_cleanly_with_the_audit_trail_flushed(tmp_path):
    """`docker stop` sends SIGTERM: the server must finish its shutdown and exit 0."""
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    child_env = {
        **os.environ,
        **env(LADDRIX_DATA=str(tmp_path / "d"), LADDRIX_PORT=str(port)),
        "LADDRIX_ORIGIN": f"http://localhost:{port}",
        "PYTHONPATH": "src",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "laddrix.tools.serve"],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("server didn't start")
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, out
    assert "stopped cleanly" in out


async def test_kite_mode_runs_kite_accounts_waiting_for_their_login(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    users = UserStore(data / "users.db", SecretBox(KEY))
    users.create_user("ayush", "Ayush", "a-long-password-1", {Role.TRADER})
    users.add_account(BrokerAccount("acc-kite", "kite", "AB1234", "ayush"))
    users.add_account(BrokerAccount("acc-paper", "paper", "P1", "ayush"))
    settings = ServeSettings.from_env(
        env(LADDRIX_DATA=str(data), LADDRIX_PRICES="kite", KITE_API_KEY="k", KITE_API_SECRET="s")
    )
    built = build(settings)
    try:
        assert sorted(built.running) == ["acc-kite", "acc-paper"] and built.not_running == {}
        assert built.kite.status(users.get_account("acc-kite")) == "login_needed"
        assert built.kite.status(users.get_account("acc-paper")) is None
        assert len(built.platform.instruments) == 0  # no simulated contracts in kite mode
        await built.kite.start()  # no saved login, no cached list: waits for the login
        assert built.kite.prices_from is None
    finally:
        await built.close()
