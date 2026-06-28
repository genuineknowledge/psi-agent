"""System tray icon for Gateway. Left-click opens browser; right-click shows menu."""

from __future__ import annotations

import contextlib
import os
import threading
import webbrowser

from loguru import logger

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    _HAS_PYSTRAY = True
except Exception:
    _HAS_PYSTRAY = False


_DEFAULT_WIDTH = 64
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dolphin.jpg")


def _create_icon_image() -> Image.Image:
    try:
        img = Image.open(_ICON_PATH).convert("RGBA")
        return img.resize((_DEFAULT_WIDTH, _DEFAULT_WIDTH), Image.Resampling.LANCZOS)
    except Exception as e:
        logger.warning(f"Failed to load tray icon {_ICON_PATH}, using fallback: {e}")
        return _create_fallback_icon_image()


def _create_fallback_icon_image() -> Image.Image:
    img = Image.new("RGBA", (_DEFAULT_WIDTH, _DEFAULT_WIDTH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    r = 14
    draw.rounded_rectangle(
        [margin, margin, _DEFAULT_WIDTH - margin, _DEFAULT_WIDTH - margin],
        radius=r,
        fill=(41, 98, 255, 255),
    )
    x = _DEFAULT_WIDTH / 2
    y = _DEFAULT_WIDTH / 2
    psi = "\u03c8"
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    draw.text((x, y), psi, fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


class GatewayTray:
    """System tray icon that provides quick access to Gateway Web Console."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._stop_event = threading.Event()
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the system tray icon in a background daemon thread."""
        if not _HAS_PYSTRAY:
            logger.warning("pystray not available, skipping system tray icon")
            return

        try:
            image = _create_icon_image()
        except Exception as e:
            logger.warning(f"Failed to create tray icon image: {e}")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem("打开控制台", self._open_browser, default=True),
                pystray.MenuItem("退出", self._quit),
            )
            self._icon = pystray.Icon("psi-agent", image, "psi-agent", menu)
        except Exception as e:
            logger.warning(f"Failed to create tray icon: {e}")
            self._icon = None
            return

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Gateway system tray icon started")

    def stop(self) -> None:
        """Stop the tray icon and wait for its thread to finish."""
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Gateway system tray icon stopped")

    def is_stop_requested(self) -> bool:
        """Returns True when user selected "退出" from the tray menu."""
        return self._stop_event.is_set()

    def _open_browser(self, icon: pystray.Icon | None = None) -> None:
        webbrowser.open(self._url)

    def _quit(self, icon: pystray.Icon | None = None) -> None:
        self._stop_event.set()
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
