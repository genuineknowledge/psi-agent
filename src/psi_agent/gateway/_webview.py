"""Native webview window for Gateway. Uses pywebview to display the Web Console.

pywebview requires its GUI loop (``webview.start()``) to run on the **main
thread** — on Windows the WinForms backend installs a SIGINT handler, which
only works on the main thread. So the webview owns the main thread while the
aiohttp server and system tray run on background threads. See ``Gateway
.run_webview()``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from loguru import logger


class GatewayWebView:
    """Manages a pywebview window for the Gateway Web Console.

    ``create()`` and ``run()`` must both be called from the main thread. The
    remaining methods (``show``/``hide``/``destroy``) dispatch onto the GUI
    loop and are safe to call from other threads (e.g. a tray callback).
    """

    def __init__(
        self,
        url: str,
        has_tray: bool = False,
        icon: str | None = None,
        on_close: Any = None,
    ) -> None:
        self._url = url
        self._has_tray = has_tray
        self._icon = icon
        self._on_close = on_close
        self._window: Any = None

    def create(self) -> None:
        """Create the webview window. Must be called on the main thread.

        Raises ImportError if pywebview is not installed.
        Raises RuntimeError if already created.
        """
        if self._window is not None:
            raise RuntimeError("GatewayWebView already created")

        webview = __import__("webview")
        self._window = webview.create_window("控制台", self._url)
        self._window.events.closing += self._on_closing  # ty: ignore
        logger.info("Gateway webview window created")

    def run(self) -> None:
        """Run the pywebview GUI loop, blocking until the window is destroyed.

        Must be called on the main thread, after ``create()``.
        """
        webview = __import__("webview")
        logger.info("Gateway webview GUI loop starting")
        webview.start(icon=self._icon)
        logger.info("Gateway webview GUI loop exited")

    def show(self, _icon: Any = None) -> None:
        """Restore a hidden webview window (called from the tray callback)."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.show()

    def hide(self) -> None:
        """Hide the webview window without destroying it."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.hide()

    def destroy(self) -> None:
        """Destroy the webview window, unblocking ``run()`` on the main thread."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.destroy()

    def _on_closing(self) -> bool:
        """Handle window close event.

        With tray: hide instead of closing, return False (keep running).
        Without tray: notify ``on_close`` and return True (allow close, which
        unblocks ``run()`` and shuts the Gateway down).
        """
        if self._has_tray:
            self.hide()
            return False
        if self._on_close is not None:
            self._on_close()
        return True
