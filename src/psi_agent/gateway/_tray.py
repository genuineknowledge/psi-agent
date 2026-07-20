"""System tray icon for Gateway. Left-click opens browser or restores webview; right-click shows menu."""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any

import anyio
from loguru import logger
from PIL import Image

from psi_agent.gateway._attention import _make_highlight_image, pulse_tray_icon
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME


class GatewayTray:
    """System tray icon. Emits "open" and "quit" events on its `events` stream."""

    def __init__(
        self,
        url: str,
        icon_path: str,
        app_name: str = DEFAULT_APP_NAME,
    ) -> None:
        self._url = url
        self._icon_path = icon_path
        self._app_name = app_name
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._normal_image: Any = None
        self._highlight_image: Any = None
        self._q: queue.Queue[str] = queue.Queue()
        self._send_stream: anyio.MemoryObjectSendStream[str] | None = None
        self._recv_stream: anyio.MemoryObjectReceiveStream[str] | None = None

    @property
    def events(self) -> anyio.MemoryObjectReceiveStream[str]:
        if self._recv_stream is None:
            raise RuntimeError("GatewayTray not started")
        return self._recv_stream

    def start(self) -> None:
        """Start the system tray icon in a background daemon thread.

        Must be called from an async context. Events will be pumped to the stream
        by a background task that must be spawned separately (see _pump_events).
        """
        if self._thread is not None:
            raise RuntimeError("GatewayTray already started")

        self._send_stream, self._recv_stream = anyio.create_memory_object_stream[str](max_buffer_size=10)

        pystray = __import__("pystray")

        try:
            image = Image.open(self._icon_path)
            self._normal_image = image
            self._highlight_image = _make_highlight_image(image)
        except Exception as e:
            logger.warning(f"Failed to load tray icon from {self._icon_path!r}: {e!r}")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem(f"打开 {self._app_name}", self._on_open, default=True),
                pystray.MenuItem("退出", self._on_quit),
            )
            self._icon = pystray.Icon("psi-agent", image, self._app_name, menu)
        except Exception as e:
            logger.warning(f"Failed to create tray icon: {e!r}")
            self._icon = None
            return

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Gateway system tray icon started")

    async def _pump_events(self) -> None:
        """Background task: read from threading.Queue and forward to anyio stream."""
        while True:
            try:
                evt: str = await anyio.to_thread.run_sync(self._q.get, abandon_on_cancel=True)
            except anyio.get_cancelled_exc_class():
                break
            try:
                await self._send_stream.send(evt)  # type: ignore[union-attr]
            except anyio.ClosedResourceError, anyio.BrokenResourceError:
                break

    def stop(self) -> None:
        """Stop the tray icon and wait for its thread to finish."""
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
            self._icon = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Gateway system tray icon stopped")

    def is_running(self) -> bool:
        """True if the tray icon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def request_attention(self) -> None:
        """Pulse tray icon (+ Windows balloon) to draw attention (best-effort)."""
        if self._icon is None or self._normal_image is None or self._highlight_image is None:
            return
        logger.info("Tray attention pulse starting")
        with contextlib.suppress(Exception):
            self._icon.notify("有对话已完成", self._app_name)
        pulse_tray_icon(self._icon, self._normal_image, self._highlight_image)

    def _on_open(self, icon: Any = None) -> None:
        self._q.put("open")

    def _on_quit(self, icon: Any = None) -> None:
        self._q.put("quit")
