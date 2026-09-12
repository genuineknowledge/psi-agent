"""Second-click activate for the Windows installer launcher (haitun.exe).

刻意为之: single-instance for the *exe* lives in ``haitun.c`` (named mutex).
This module only answers that second click: when the launcher finds the mutex
already held, it ``SetEvent`` on a fixed named Event; a tray-mode Gateway
listens and re-opens the console (browser / webview), like Cursor / Feishu.

Terminal multi-Gateway is unchanged — no mutex there; AppData lock is the
memory-zone gate. Event name must match ``haitun.c`` byte-for-byte.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from typing import Any

from loguru import logger

# Keep in sync with .github/inno-setup/haitun.c HAITUN_ACTIVATE_EVENT.
ACTIVATE_EVENT_NAME = "Local\\GenuineKnowledge.HaitunAgent.Activate"


def _kernel32() -> Any:
    # ``windll`` / ``WinDLL`` are Windows-only; getattr so Linux CI ``ty`` resolves.
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise RuntimeError("ctypes.windll is only available on Windows")
    return windll.kernel32


class InstallerActivateListener:
    """Wait on the installer activate Event; call *on_activate* each signal."""

    def __init__(self, on_activate: Callable[[], None]) -> None:
        self._on_activate = on_activate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._event: Any = None

    def start(self) -> None:
        if sys.platform != "win32":
            return
        if self._thread is not None:
            raise RuntimeError("InstallerActivateListener already started")
        kernel32 = _kernel32()
        # Auto-reset: one SetEvent → one awaken (bManualReset=False).
        handle = kernel32.CreateEventW(None, False, False, ACTIVATE_EVENT_NAME)
        if not handle:
            err = int(kernel32.GetLastError())
            logger.warning(
                f"Installer activate Event create failed (error={err}); "
                "second haitun.exe click will not reopen the console"
            )
            return
        self._event = handle
        self._thread = threading.Thread(
            target=self._run,
            name="haitun-installer-activate",
            daemon=True,
        )
        self._thread.start()
        logger.info("Installer activate listener started (second-click reopen)")

    def stop(self) -> None:
        self._stop.set()
        if sys.platform == "win32" and self._event is not None:
            # Wake the waiter so it can exit promptly.
            _kernel32().SetEvent(self._event)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        if sys.platform == "win32" and self._event is not None:
            _kernel32().CloseHandle(self._event)
            self._event = None

    def _run(self) -> None:
        kernel32 = _kernel32()
        wait_ms = 500
        while not self._stop.is_set():
            # WAIT_OBJECT_0 == 0; WAIT_TIMEOUT == 258.
            result = kernel32.WaitForSingleObject(self._event, wait_ms)
            if self._stop.is_set():
                break
            if result != 0:
                continue
            try:
                self._on_activate()
            except Exception as e:
                logger.warning(f"Installer activate handler failed: {e!r}")
