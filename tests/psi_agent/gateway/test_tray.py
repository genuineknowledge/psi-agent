from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

import pytest
from PIL import Image as PILImage

from psi_agent.gateway._tray import GatewayTray

_IS_WINDOWS = sys.platform == "win32"


class _MissingDisplayNameError(Exception):
    pass


DisplayNameError: type[BaseException] = _MissingDisplayNameError
_HAS_X11_SUPPORT = False
_HAS_X11 = False
if not _IS_WINDOWS:
    try:
        xlib_error = __import__("Xlib.error", fromlist=["DisplayNameError"])
        xdisplay = __import__("Xlib.display", fromlist=["Display"])
    except ImportError:
        pass
    else:
        DisplayNameError = xlib_error.DisplayNameError
        _HAS_X11_SUPPORT = True
        try:
            xdisplay.Display()
            _HAS_X11 = True
        except DisplayNameError:
            pass


@pytest.fixture
def icon_file(tmp_path: Path) -> str:
    img = PILImage.new("RGBA", (64, 64), (41, 98, 255, 255))
    path = str(tmp_path / "icon.png")
    img.save(path, "PNG")
    return path


def test_gateway_tray_init(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    assert tray._url == "http://127.0.0.1:8888"
    assert tray._icon_path == icon_file
    assert tray._q is not None


def test_gateway_tray_stop_when_not_started(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.stop()


def test_gateway_tray_quit_callback_puts_event(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray._on_quit()
    assert tray._q.get() == "quit"


@pytest.mark.skipif(
    _IS_WINDOWS or not _HAS_X11_SUPPORT,
    reason="requires python-xlib on a non-Windows platform",
)
def test_gateway_tray_start_no_display(icon_file: str) -> None:
    """start() raises when no X11 display available."""
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    if not _HAS_X11:
        with pytest.raises(DisplayNameError):
            tray.start()
    else:
        tray.start()
        with contextlib.suppress(Exception):
            tray.stop()


@pytest.mark.skipif(
    _IS_WINDOWS or not _HAS_X11,
    reason="requires an active X11 display on a non-Windows platform",
)
def test_gateway_tray_thread_terminates_on_stop(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.start()

    time.sleep(0.3)

    tray.stop()

    assert not tray.is_running()
