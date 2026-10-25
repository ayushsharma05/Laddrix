"""No secret in any log line (#367).

The flows that touch secrets run with every logger captured at DEBUG: logins (right and wrong),
a password change, step-up, an admin's new user and resets (one-time passwords, authenticator
links), the broker's daily login (request token, API secret, access token), a webhook whose
address and signing key are secret, failing webhooks. Then every captured line, with its
exception text, is searched for each secret.
"""

import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from test_market_alerts import sender_with
from test_server import PASSWORD, World
from test_users import kite_app, session_server

from laddrix import logsafe
from laddrix.broker.kite.daily_login import KiteDailyLogin
from laddrix.broker.kite.session import KiteSession
from laddrix.config import KiteSettings
from laddrix.domain.accounts import BrokerAccount
from laddrix.logsafe import HIDDEN, Redact, redact
from laddrix.ops.alerts import Alert
from laddrix.ops.notify import WebhookNotifier

HOOK = "https://hooks.example.com/services/T0001/B0002/x8KqSecretPathPart9z"


def text_of(caplog):
    lines = []
    for record in caplog.records:
        lines.append(record.getMessage())
        if record.exc_info:
            lines.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(lines)


@pytest.fixture
def production_logging(caplog):
    """The server's log setup (logsafe.install) around caplog's handler, undone afterwards."""
    caplog.set_level(logging.DEBUG)
    levels = {n: logging.getLogger(n).level for n in logsafe.QUIET}
    logsafe.install()
    yield caplog
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
    for logger in map(logging.getLogger, logsafe.WATCHED):
        for owner in (logger, *logger.handlers):
            for f in [f for f in owner.filters if isinstance(f, Redact)]:
                owner.removeFilter(f)


async def test_the_flows_that_touch_secrets_log_none_of_them(tmp_path, nifty_fut,
                                                            production_logging):  # fmt: skip
    caplog = production_logging
    secrets = [PASSWORD]
    w = World(tmp_path, nifty_fut)
    secrets += list(w.secrets.values())  # the authenticator secrets

    # logins, right and wrong; a password change; step-up, right and wrong
    admin = await w.logged_in("ayush")
    secrets.append(admin.cookies["__Host-mt_session"])
    bad = await w.client().post("/api/session", json={"username": "riya", "password": PASSWORD,
                                                      "code": "000000"})  # fmt: skip
    assert bad.status_code == 401
    trader = await w.logged_in("riya")
    new_password = "a brand new long password"
    secrets.append(new_password)
    r = await trader.post("/api/me/password", json={"old": PASSWORD, "new": new_password})
    assert r.status_code == 204
    assert (await admin.post("/api/me/step-up", json={"code": "123456"})).status_code == 403
    code = w.code("ayush")
    secrets.append(code)
    assert (await admin.post("/api/me/step-up", json={"code": code})).status_code == 204

    # an admin's new user and resets: one-time passwords and authenticator links
    made = (await admin.post("/api/admin/users", json={"id": "mala", "name": "Mala",
                                                       "roles": ["trader"]})).json()  # fmt: skip
    secrets += [made["temporary_password"], parse_qs(urlparse(made["totp_uri"]).query)["secret"][0]]
    reset = (await admin.post("/api/admin/users/mala/reset-password", json={})).json()
    secrets.append(reset["temporary_password"])
    uri = (await admin.post("/api/admin/users/mala/reset-authenticator", json={})).json()
    secrets.append(parse_qs(urlparse(uri["totp_uri"]).query)["secret"][0])

    # the broker's daily login: request token in, access token out; the API secret in between
    login = KiteDailyLogin(kite_app, tmp_path, http_factory=session_server("AB1234"))
    account = BrokerAccount("acc-1", "kite", "AB1234", "ayush")
    await login.complete(account, "request-token-77abc")
    secrets += ["request-token-77abc", "tok-123", "secret1"]
    logging.getLogger("laddrix.test").info("objects: %r %r", KiteSettings("k", "secret1", None),
                                             KiteSession("AB1234", "tok-123"))  # fmt: skip

    # webhooks: the address and the signing key are secret; failures are logged
    sender, _ = sender_with(lambda request: httpx.Response(500))
    sender.send(HOOK, "whsec-signing-key-55", {"text": "x"}, lambda _e: None)
    secrets += ["x8KqSecretPathPart9z", "whsec-signing-key-55"]

    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    notifier = WebhookNotifier(
        HOOK, client=httpx.AsyncClient(transport=httpx.MockTransport(broken))
    )
    notifier(Alert("disk", "ERROR", "disk low", True, w.clock.now))
    await notifier.aclose()
    await sender.aclose()
    await w.log.flush()

    text = text_of(caplog)
    assert text, "nothing was logged: the test would prove nothing"
    for secret in secrets:
        assert secret not in text, f"a secret reached the logs: {secret[:4]}…"
    # the filter is a safety net: our own code never needed it
    caught = [r.getMessage() for r in caplog.records
              if r.name.startswith("laddrix") and HIDDEN in r.getMessage()]  # fmt: skip
    assert caught == []


def test_redaction_of_each_kind():
    cases = {
        '127.0.0.1:5 - "GET /kite/callback?request_token=abc123&state=st9&status=success '
        'HTTP/1.1" 303': ("abc123", "st9"),
        'POST {"username": "a", "password": "hunter2hunter2", "code": "123456"}': (
            "hunter2hunter2",
            "123456",
        ),
        "Authorization: token key1:acc-tok-9": ("acc-tok-9",),
        "sealed gAAAAABmZ8Xq0123456789abcdefghijklmnop end": ("gAAAAABmZ8Xq0123456789",),
        "otpauth://totp/Laddrix:ayush?secret=JBSWY3DPEHPK3PXP": ("JBSWY3DPEHPK3PXP",),
        f"POST {HOOK} failed": ("x8KqSecretPathPart9z", "/services/"),
    }
    for line, hidden in cases.items():
        clean = redact(line)
        assert HIDDEN in clean, line
        for secret in hidden:
            assert secret not in clean, (line, clean)
    assert "hooks.example.com" in redact(f"POST {HOOK}")  # the host stays: useful, not secret
    assert redact("order MT12 filled 75 @ 25,010.00") == "order MT12 filled 75 @ 25,010.00"


def test_the_filter_rewrites_messages_args_and_tracebacks():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/kite/callback?request_token=zz99", "1.1",
                                303), None)  # fmt: skip
    assert Redact().filter(record)
    assert "zz99" not in record.getMessage() and HIDDEN in record.getMessage()
    try:
        raise RuntimeError("login failed for password=pw-in-error")
    except RuntimeError:
        import sys

        failing = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None,
                                    sys.exc_info())  # fmt: skip
    Redact().filter(failing)
    assert "pw-in-error" not in failing.exc_text


def test_install_covers_the_web_server_and_quiets_url_loggers():
    import uvicorn
    from fastapi import FastAPI

    uvicorn.Config(FastAPI(), log_level="info")  # sets up uvicorn's own loggers, as serve does
    logsafe.install()
    logsafe.install()  # twice: still one filter each
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, Redact) for f in access.filters) == 1
    assert all(any(isinstance(f, Redact) for f in h.filters) for h in access.handlers)
    for name in logsafe.QUIET:  # set on the logger itself: not left to whatever the root says
        assert logging.getLogger(name).level == logging.WARNING


def test_objects_holding_secrets_dont_print_them(tmp_path):
    from laddrix.tools.serve import ServeSettings

    shown = repr(KiteSettings("key1", "api-secret-1", tmp_path, "http://u:proxypass@h:3128"))
    shown += repr(KiteSession("AB1234", "access-tok-1"))
    shown += repr(ServeSettings(secret_key="srv-key-1", origin="https://x.example"))
    for secret in ("api-secret-1", "proxypass", "access-tok-1", "srv-key-1"):
        assert secret not in shown
    assert "key1" in shown and "AB1234" in shown  # the harmless parts stay, for debugging


def test_the_access_log_still_formats_after_cleaning(caplog):
    """The web server's access formatter unpacks the parts itself: they stay a tuple."""
    import uvicorn
    from fastapi import FastAPI
    from uvicorn.logging import AccessFormatter

    uvicorn.Config(FastAPI(), log_level="info")
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/kite/callback?request_token=zz99&state=s1",
                                "1.1", 303), None)  # fmt: skip
    assert Redact().filter(record)
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert "zz99" not in line and "s1" not in line and "/kite/callback?request_token=" in line
    assert "303" in line
