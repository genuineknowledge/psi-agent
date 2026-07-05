"""Native webview window for Gateway. Uses pywebview to display the Web Console."""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from loguru import logger


class GatewayWebView:
    """Manages a pywebview window for the Gateway Web Console.

    Runs pywebview in a background daemon thread. The main anyio loop
    can ``to_thread.run_sync(wv.wait_closed)`` to block until the window
    is closed by the user.
    """

    def __init__(self, url: str, has_tray: bool = False) -> None:
        self._url = url
        self._has_tray = has_tray
        self._window: Any = None
        self._closed_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the webview window in a background daemon thread.

        Raises ImportError if pywebview is not installed.
        Raises RuntimeError if already started.
        """
        if self._thread is not None:
            raise RuntimeError("GatewayWebView already started")

        webview = __import__("webview")

        self._window = webview.create_window("psi-agent Gateway", self._url)
        self._window.events.closing += self._on_closing

        self._thread = threading.Thread(target=webview.start, daemon=True)
        self._thread.start()
        logger.info("Gateway webview window started")

    def show(self, _icon: Any = None) -> None:
        """Restore a previously hidden webview window (called from tray callback)."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.show()

    def stop(self) -> None:
        """Destroy the webview window and wait for the thread to finish."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.destroy()
            self._window = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Gateway webview window stopped")

    def wait_closed(self) -> None:
        """Block (in a worker thread) until the webview window is closed."""
        self._closed_event.wait()

    def is_running(self) -> bool:
        """True if the webview thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _on_closing(self) -> bool:
        """Handle window close event.

        Returns True to allow closing, False to prevent it.
        With tray: hide instead of closing, return False.
        Without tray: signal close and return True.
        """
        if self._has_tray:
            if self._window is not None:
                self._window.hide()
            return False
        self._closed_event.set()
        return True
