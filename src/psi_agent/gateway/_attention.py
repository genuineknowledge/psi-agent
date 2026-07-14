"""Desktop attention cues when a chat turn completes in the background."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import TYPE_CHECKING, Any

import anyio
from loguru import logger
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from psi_agent.gateway._tray import GatewayTray
    from psi_agent.gateway._webview import GatewayWebView


def _make_highlight_image(image: Any) -> Any:
    """Bright orange tray frame — solid tint alone is too subtle on multi-size .ico."""
    base = image.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (255, 140, 0, 0))
    border = max(2, min(w, h) // 6)
    draw = ImageDraw.Draw(overlay)
    for i in range(border):
        draw.rectangle((i, i, w - 1 - i, h - 1 - i), outline=(255, 140, 0, 255))
    return Image.alpha_composite(base, overlay)


def _flash_hwnd(hwnd: int) -> None:
    """Flash a native window on the Windows taskbar (no-op elsewhere)."""
    if sys.platform != "win32":
        return

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    info = FLASHWINFO()
    info.cbSize = ctypes.sizeof(FLASHWINFO)
    info.hwnd = hwnd
    # FLASHW_TRAY | FLASHW_TIMERNOFG — flash taskbar until window comes foreground.
    info.dwFlags = 0x02 | 0x0C
    info.uCount = 5
    info.dwTimeout = 0
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def pulse_tray_icon(icon: Any, normal_image: Any, highlight_image: Any) -> None:
    """Swap tray icons a few times in a daemon thread (best-effort)."""

    def _pulse() -> None:
        for _ in range(3):
            try:
                icon.icon = highlight_image
                time.sleep(0.35)
                icon.icon = normal_image
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"Tray attention pulse failed: {e!r}")
                break

    threading.Thread(target=_pulse, daemon=True).start()


class AttentionHub:
    """Routes SPA ``POST /ui/attention`` to tray / webview native cues."""

    def __init__(self) -> None:
        self._tray: GatewayTray | None = None
        self._webview: GatewayWebView | None = None

    def bind(
        self,
        *,
        tray: GatewayTray | None = None,
        webview: GatewayWebView | None = None,
    ) -> None:
        if tray is not None:
            self._tray = tray
        if webview is not None:
            self._webview = webview

    def notify_sync(self) -> None:
        if self._webview is not None:
            self._webview.request_attention()
        if self._tray is not None:
            self._tray.request_attention()
        if self._webview is None and self._tray is None:
            logger.debug("Attention notify with no tray/webview bound")
        else:
            logger.info("Attention notify dispatched")

    def schedule_notify(self) -> None:
        """Fire-and-forget: never block the aiohttp event loop (pystray can stall threads)."""
        threading.Thread(target=self.notify_sync, name="ui-attention", daemon=True).start()

    async def notify(self) -> None:
        # Return immediately; icon pulse / FlashWindowEx run on a daemon thread.
        self.schedule_notify()
        await anyio.lowlevel.checkpoint()
