"""Logs as JSON lines, each carrying the ids that tie it to a request and an order (#287).

    {"ts": "2026-10-24T09:15:02.113Z", "level": "WARNING", "logger": "laddrix.orders.manager",
     "msg": "place MT12: no clear answer: ...", "request_id": "a1b2c3d4e5f60718",
     "user": "ayush", "account_id": "acc-ayush", "client_order_id": "MT12"}

Where the ids come from:

    a web request ─► the server's middleware: request_id (the caller's X-Request-ID if it is
                     short and plain, else a new one; sent back in the answer's header)
                 ─► signing in is checked: user
    an order     ─► the order manager, while it places, changes, cancels or applies a
                     broker update: account_id, client_order_id (and broker_order_id)

The ids live in a context variable, so every line logged while they are set carries them,
in this task and in the tasks it starts. Following one order through the server's log:

    docker compose -f deploy/compose.yaml logs --no-log-prefix app \\
      | jq -c 'select(.client_order_id == "MT12")'

The audit trail stays the record of what happened; logs are for finding problems. Every
line still passes the secret filter (logsafe.py) before it is written.
"""

import contextlib
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import orjson

IDS = ("request_id", "user", "account_id", "client_order_id", "broker_order_id")
FORMATS = ("json", "text")
_context: ContextVar[dict[str, str] | None] = ContextVar("laddrix_log_ids", default=None)
_PLAIN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ACCESS = ("client", "method", "path", "http_version", "status")  # uvicorn's access line


def ids() -> dict[str, str]:
    """The ids set for what runs now."""
    return dict(_context.get() or {})


@contextlib.contextmanager
def bound(**more: str | None) -> Iterator[None]:
    """Lines logged inside carry these ids too (None values are left out)."""
    added = {k: str(v) for k, v in more.items() if v is not None}
    token = _context.set({**(_context.get() or {}), **added})
    try:
        yield
    finally:
        _context.reset(token)


def start_request(request_id: str | None) -> str:
    """A new web request: its own ids, from here on in this task. Returns its request id."""
    rid = request_id if request_id and _PLAIN_ID.fullmatch(request_id) else uuid.uuid4().hex[:16]
    _context.set({"request_id": rid})
    return rid


def note(**more: str) -> None:
    """Add ids to the request being answered. The dict is shared with the request's other
    tasks on purpose: a check run on a worker thread (who signed in) still reaches the
    request's later lines."""
    current = _context.get()
    if current is not None:
        current.update(more)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time (UTC), level, logger, message, the ids, any error."""

    def format(self, record: logging.LogRecord) -> str:
        at = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        line: dict[str, Any] = {
            "ts": at.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        line.update(_context.get() or {})
        for key in IDS:  # ids passed as extra= on one line win over the context
            value = getattr(record, key, None)
            if value is not None:
                line[key] = str(value)
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            line.update(zip(_ACCESS, record.args, strict=False))  # searchable: .status >= 500
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line["error"] = record.exc_text
        return orjson.dumps(line, default=str).decode()


TEXT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup(fmt: str = "json", stream=None) -> logging.Handler:
    """Every log line to stderr in this format, the web server's too (start it with
    log_config=None so its lines reach here). Replaces any handler set before."""
    if fmt not in FORMATS:
        raise ValueError(f"log format must be one of {', '.join(FORMATS)}, got {fmt!r}")
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler
