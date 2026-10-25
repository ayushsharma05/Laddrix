"""Keep today's Kite access token in a private local file between commands.

The token is a live key to the account until 6 AM next day, so the file is created
readable by its owner only, lives in the git-ignored data/ folder, and is treated
as expired once the 6 AM (India time) cutoff has passed.
"""

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from laddrix.broker.kite.session import KiteSession

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_TIME = time(6, 0)


def expires_at(created: datetime) -> datetime:
    """The first 06:00 India time after `created`."""
    local = created.astimezone(IST)
    cutoff = datetime.combine(local.date(), EXPIRY_TIME, tzinfo=IST)
    return cutoff if local < cutoff else cutoff + timedelta(days=1)


def save(path: Path, session: KiteSession, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "user_id": session.user_id,
        "access_token": session.access_token,
        "created_at": now.isoformat(),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load(path: Path, now: datetime) -> KiteSession | None:
    """Today's session, or None if there is none or it has expired."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(payload["created_at"])
    if now >= expires_at(created):
        return None
    return KiteSession(user_id=payload["user_id"], access_token=payload["access_token"])
