"""Exclusive Gateway ownership of one AppData root.

刻意为之: identity is the resolved AppData path only — not listen port, not PID
as a durable id. Two processes on the same AppData both rewrite
``state/latest.json`` (last writer wins); that wiped session registries in the
field. Listen port is per-process HTTP plumbing and must not define the
instance.

Different ``--appdata`` → independent locks (both may run). Pipe collisions
are a separate axis (``--socket-path``); see gateway/AGENTS.md.
"""

from __future__ import annotations

import contextlib
import os
import sys
from types import TracebackType
from typing import BinaryIO, cast

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def instance_lock_path(appdata_root: str) -> str:
    """Lock file under the AppData root (``{appdata}/gateway.instance.lock``)."""
    return os.path.join(appdata_root, "gateway.instance.lock")


class GatewayInstanceError(Exception):
    """Another live process already holds the AppData instance lock.

    User-facing ``str`` stays free of OS errno noise; chain the original
    ``OSError`` with ``raise ... from e`` for logs / traceback.
    """

    def __init__(self, appdata_root: str) -> None:
        self.appdata_root = appdata_root
        super().__init__(
            f"Another Gateway is already using AppData {appdata_root!r}. "
            "Quit that process, or start this one with a different --appdata "
            "(and a different --socket-path if both should run)."
        )


class AppDataInstanceLock:
    """Hold an OS-level exclusive lock on ``{appdata}/gateway.instance.lock``.

    The lock is released when ``release()`` runs or the process exits (OS drops
    flock / msvcrt locks with the fd). A leftover lock *file* after a crash is
    harmless — the next process can lock it again.
    """

    def __init__(self, appdata_root: str) -> None:
        self.appdata_root = appdata_root
        self.path = instance_lock_path(appdata_root)
        self._fh: BinaryIO | None = None

    def acquire(self) -> None:
        if self._fh is not None:
            raise RuntimeError("AppDataInstanceLock already acquired")
        os.makedirs(self.appdata_root, exist_ok=True)
        # Keep the fd open for the process lifetime (OS releases the lock on
        # close / exit). Use os.open so we do not need a context-managed open().
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            fh = cast(BinaryIO, os.fdopen(fd, "r+b"))
        except OSError as e:
            # Windows: another process may hold the file exclusively enough that
            # open itself fails — treat as the same conflict.
            raise GatewayInstanceError(self.appdata_root) from e
        try:
            if os.path.getsize(self.path) == 0:
                fh.write(b"\0")
                fh.flush()
            fh.seek(0)
            _lock_exclusive_nb(fh)
        except OSError as e:
            fh.close()
            raise GatewayInstanceError(self.appdata_root) from e

        with contextlib.suppress(OSError):
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()}\n".encode())
            fh.flush()
        self._fh = fh

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        with contextlib.suppress(OSError):
            _unlock(fh)
        with contextlib.suppress(OSError):
            fh.close()

    def held_pid_line(self) -> str:
        """Diagnostics: pid line written into the lock file (holder only)."""
        fh = self._fh
        if fh is None:
            return ""
        fh.seek(0)
        return fh.read().decode()

    def __enter__(self) -> AppDataInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _lock_exclusive_nb(fh: BinaryIO) -> None:
    fd = fh.fileno()
    if sys.platform == "win32":
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: BinaryIO) -> None:
    fd = fh.fileno()
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            fh.seek(0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
