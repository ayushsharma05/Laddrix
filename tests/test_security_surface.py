"""What the production server never hands out (#279, part of the release gate).

A real server (tools/serve.build) is asked, without logging in, for the files an attacker
tries first: environment files, the git folder, databases, backups, the secret key, the
lock, API docs, directory listings, and the same through path tricks. Secrets planted in the
data folder and the UI folder must never come back. The built screens (and the desktop app's
sources) must hold no server secret.
"""

import re
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
PLANTED = "PLANTED-SECRET-7f3a9c"  # in every secret file below: must never be served

PROBES = [
    "/.env", "/.git/config", "/.git/HEAD", "/users.db", "/events.db", "/secret.key",
    "/server.lock", "/backups/", "/data/users.db", "/kite_token_acc-1.json",
    "/docs", "/redoc", "/openapi.json", "/api/docs", "/api/openapi.json", "/api/debug",
    "/..%2f.env", "/%2e%2e/.env", "/assets/../../.env", "/assets/..%2f..%2f.env",
    "/static/../.env", "//etc/passwd", "/assets/", "/index.html/../.env",
]  # fmt: skip


@pytest.fixture
def server(tmp_path):
    from test_serve import env

    from laddrix.tools.serve import ServeSettings, build

    ui, data = tmp_path / "ui", tmp_path / "data"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>Laddrix</html>")
    (ui / "assets" / "app.js").write_text("console.log('screens')")
    (tmp_path / ".env").write_text(f"LADDRIX_SECRET_KEY={PLANTED}")  # next to the UI folder
    data.mkdir()
    for name in ("secret.key", ".env", "kite_token_acc-1.json"):
        (data / name).write_text(PLANTED)
    built = build(ServeSettings.from_env(env(LADDRIX_DATA=str(data), LADDRIX_UI=str(ui))))
    yield built
    if getattr(built, "fence", None) is not None:  # the data folder's owner lock (#273)
        built.fence.release()


async def test_no_secret_file_or_private_path_is_ever_served(server):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://trade.example.in") as c:
        for path in PROBES:
            r = await c.get(path)
            assert PLANTED not in r.text, path
            assert r.status_code in (200, 401, 404, 405), (path, r.status_code)
            if r.status_code == 200:  # only the screens' own page, never a file listing
                assert r.text == "<html>Laddrix</html>", path
        assert (await c.get("/api/health")).json()["status"] == "ok"


SECRET_PATTERNS = [
    re.compile(r"api_secret\s*[:=]\s*['\"][A-Za-z0-9]{8,}"),
    re.compile(r"LADDRIX_SECRET_KEY\s*=\s*\S{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"access_token['\"]?\s*[:=]\s*['\"][A-Za-z0-9]{16,}"),
]


def test_the_screens_and_desktop_app_hold_no_server_secret():
    """Everything shipped to a browser or a desktop can be read by its user: no broker secret,
    key or token may be in it (the server keeps them)."""
    shipped = [p for d in ("ui/src", "ui/dist", "desktop") for p in (ROOT / d).rglob("*")
               if p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".cjs", ".mjs", ".json",
                                                ".html", ".map"}
               and "node_modules" not in p.parts]  # fmt: skip
    assert shipped, "nothing found to check"
    for path in shipped:
        text = path.read_text(errors="ignore")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"{path.relative_to(ROOT)}: {pattern.pattern}"


def test_no_env_file_or_key_is_tracked_in_git():
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()  # fmt: skip
    bad = [f for f in tracked if re.search(r"(^|/)\.env$|\.key$|kite_token_.*\.json$|\.db$", f)]
    assert bad == []
