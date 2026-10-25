"""Logs never hold a secret (#367).

    every log line ─► Redact (a logging filter) ─► the handler that writes it

What it hides, wherever it appears in the formatted line:
- values of secret-looking parameters in URLs and forms: request_token, access_token, token,
  api_secret, secret, password, checksum, code, state (the Kite login callback's URL is in the
  web server's access log);
- `Authorization: token key:secret` headers;
- JSON fields named like secrets ("password": "...");
- encrypted values (Fernet tokens) and authenticator links (otpauth://...);
- the path and query of any full web address (https://host/...): a webhook's address often
  *is* its secret (https://hooks.example.com/services/T0/B0/x8Kq...). The host stays.

And two sources are kept quiet or blind instead of filtered:
- httpx and httpcore log every request's full URL at INFO; a user's webhook URL can carry its
  own secret in the path, so only their warnings are kept;
- classes holding secrets (KiteSettings, KiteSession, ServeSettings) leave them out of their
  repr, so an exception or a debug line that prints the object prints no secret.

A filter is a safety net, not the rule: code still never logs a secret on purpose
(tests/test_log_redaction.py runs the flows that touch secrets and searches every log line).
"""

import logging
import re

HIDDEN = "[redacted]"
_PARAM = re.compile(
    r"(?i)\b(request_token|access_token|refresh_token|enctoken|token|api_secret|secret|"
    r"password|checksum|code|state)=([^&\s\"']+)"
)
_JSON = re.compile(
    r"(?i)(\"(?:password|new|old|temporary_password|secret|api_secret|access_token|"
    r"totp_secret|token|code)\"\s*:\s*)\"[^\"]*\""
)
_AUTH = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?token\s+)[^\s'\",]+")
_FERNET = re.compile(r"gAAAAA[A-Za-z0-9_\-=]{20,}")
_OTPAUTH = re.compile(r"otpauth://\S+")
_URL = re.compile(r"(https?://[^/\s'\"<>]+)/[^\s'\"<>)]*")


def redact(text: str) -> str:
    text = _PARAM.sub(lambda m: f"{m[1]}={HIDDEN}", text)
    text = _JSON.sub(lambda m: f'{m[1]}"{HIDDEN}"', text)
    text = _AUTH.sub(lambda m: f"{m[1]}{HIDDEN}", text)
    text = _FERNET.sub(HIDDEN, text)
    text = _OTPAUTH.sub(f"otpauth://{HIDDEN}", text)
    return _URL.sub(lambda m: f"{m[1]}/{HIDDEN}", text)


class Redact(logging.Filter):
    """Rewrites each record's message (and the exception text) with secrets hidden.

    The message's parts are cleaned where they are, not merged into one string: some
    formatters read the parts themselves (the web server's access log unpacks `args` into
    client, method, path...). Only when a secret appears in the joined text alone is the
    message replaced by the cleaned whole."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v
                           for k, v in record.args.items()}  # fmt: skip
        message = record.getMessage()
        clean = redact(message)
        if clean != message:  # a secret only the joined text shows (never the access log's)
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


QUIET = ("httpx", "httpcore")  # they log full URLs (a webhook's secret path) at INFO
WATCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access")  # the root, and the web server


def install() -> None:
    """Put the filter on every handler that writes logs (the root's and the web server's),
    and on the web server's access logger. Call after uvicorn has set up its logging."""
    redacting = Redact()
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in WATCHED:
        logger = logging.getLogger(name)
        if not any(isinstance(f, Redact) for f in logger.filters):
            logger.addFilter(redacting)
        for handler in logger.handlers:
            if not any(isinstance(f, Redact) for f in handler.filters):
                handler.addFilter(redacting)
