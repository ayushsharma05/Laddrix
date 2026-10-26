"""One trading server per data folder (#273): a second one can't trade the same accounts.

    start ─► take an exclusive lock on <data>/server.lock (held for the life of the process)
          ─► another process holds it ─► refuse to start, naming its process id
          ─► taken: write our process id and start time into it (for that message)

Why a lock, not just "don't start two": a second server on the same data would replay the
same audit trail, reconcile the same accounts and send orders as the same owner. The lock is
the operating system's (flock): it is released when the process ends, however it ends (a
crash, a kill), so a dead server never blocks the next one; and a paused or hung one keeps it,
so nothing can take over from a server that might wake up and send. Taking over a stuck
server means ending it first (docs/runbooks/incidents.md).

This fences one machine, which is how Laddrix is deployed (one server, one static IP the
broker accepts orders from). Two machines would need the lock in a shared service.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

LOCK = "server.lock"


class FenceError(RuntimeError):
    """Another server owns this data folder."""


if sys.platform == "win32":  # Windows has no flock: lock the file's first byte instead (#464)
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BlockingIOError from None

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _holder(fd: int) -> str:
    """Who holds the lock, as it wrote itself down ("unknown" if Windows won't let us read it)."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 200).decode(errors="replace").strip() or "unknown"
    except OSError:
        return "unknown"


class DataFence:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / LOCK
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(fd)
        except BlockingIOError:
            holder = _holder(fd)
            os.close(fd)
            raise FenceError(
                f"another Laddrix server owns {self.path.parent} ({holder}): only one may trade "
                "its accounts. Stop it first; if it is hung, end its process"
            ) from None
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"pid {os.getpid()} since {datetime.now(UTC).isoformat()}".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None
