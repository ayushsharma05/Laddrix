"""Settings read from the environment (and an optional .env file for local use).

Secrets never live in code or git. On the server they come from the environment;
on a laptop, from a git-ignored `.env` file with lines like `KITE_API_KEY=...`.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """A required setting is missing or invalid. The message names it."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Blank lines and # comments are ignored; quotes are stripped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"{path}:{number}: expected KEY=VALUE")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def environment(dotenv: Path = Path(".env")) -> dict[str, str]:
    """The real environment, with .env values filling in anything not already set."""
    merged = read_dotenv(dotenv)
    merged.update(os.environ)
    return merged


@dataclass(frozen=True)
class KiteSettings:
    api_key: str
    api_secret: str = field(repr=False)  # never in a log or an error message (#367)
    token_file: Path
    # a proxy URL: this account's own IP; None: the server's (#372). Hidden too: a proxy
    # address can carry its own password (http://user:pass@host)
    egress: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "KiteSettings":
        missing = [k for k in ("KITE_API_KEY", "KITE_API_SECRET") if not env.get(k)]
        if missing:
            raise ConfigError(f"missing settings: {', '.join(missing)} (put them in .env)")
        return cls(
            api_key=env["KITE_API_KEY"],
            api_secret=env["KITE_API_SECRET"],
            token_file=Path(env.get("KITE_TOKEN_FILE", "data/kite_token.json")),
        )
