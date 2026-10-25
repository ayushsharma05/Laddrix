"""Settings from the environment and a local .env file (config.py): parsing, which wins, missing
ones named.
"""

import pytest

from laddrix.config import ConfigError, KiteSettings, environment, read_dotenv


def test_dotenv_parsing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\n\nKITE_API_KEY=abc\nKITE_API_SECRET = 'sec'\n")
    assert read_dotenv(path) == {"KITE_API_KEY": "abc", "KITE_API_SECRET": "sec"}


def test_bad_dotenv_line_names_the_line(tmp_path):
    path = tmp_path / ".env"
    path.write_text("JUSTTEXT\n")
    with pytest.raises(ConfigError, match=":1:"):
        read_dotenv(path)


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("KITE_API_KEY=from-file\n")
    monkeypatch.setenv("KITE_API_KEY", "from-env")
    assert environment(path)["KITE_API_KEY"] == "from-env"


def test_missing_settings_are_named():
    with pytest.raises(ConfigError, match="KITE_API_SECRET"):
        KiteSettings.from_env({"KITE_API_KEY": "abc"})


def test_settings_defaults():
    settings = KiteSettings.from_env({"KITE_API_KEY": "a", "KITE_API_SECRET": "b"})
    assert str(settings.token_file).endswith("kite_token.json")
