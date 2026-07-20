"""Desktop attention cues when a chat turn completes in the background."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING, Any

from loguru import logger
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from psi_agent.gateway._tray import GatewayTray
    from psi_agent.gateway._webview import WebViewProcess


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
        self._webview: WebViewProcess | None = None

    def bind(
        self,
        *,
        tray: GatewayTray | None = None,
        webview: WebViewProcess | None = None,
    ) -> None:
        if tray is not None:
            self._tray = tray
        if webview is not None:
            self._webview = webview

    def notify_sync(self) -> None:
        if self._webview is not None:
            with contextlib.suppress(Exception):
                self._webview.send_sync("flash")
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
        # Do not await anyio.lowlevel.checkpoint() — ty cannot resolve that attribute.
        self.schedule_notify()
