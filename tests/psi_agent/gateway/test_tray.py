from __future__ import annotations

import contextlib
import time
from pathlib import Path

import pytest
from PIL import Image as PILImage

from psi_agent.gateway._tray import GatewayTray


def _load_display_name_error() -> type[BaseException]:
    try:
        xlib_error = __import__("Xlib.error", fromlist=["DisplayNameError"])
    except ModuleNotFoundError:
        return RuntimeError
    display_name_error = getattr(xlib_error, "DisplayNameError", RuntimeError)
    if isinstance(display_name_error, type) and issubclass(display_name_error, BaseException):
        return display_name_error
    return RuntimeError


DisplayNameError = _load_display_name_error()


def _has_x11_display() -> bool:
    try:
        xlib_display = __import__("Xlib", fromlist=["display"])
        display_module = getattr(xlib_display, "display", None)
        display_ctor = getattr(display_module, "Display", None)
        if not callable(display_ctor):
            return False
        display_ctor()
        return True
    except DisplayNameError, ModuleNotFoundError:
        return False


_HAS_X11 = _has_x11_display()


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
    assert tray._stop_event is not None


def test_gateway_tray_stop_when_not_started(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.stop()


def test_gateway_tray_quit_callback_sets_stop_event(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray._quit()
    assert tray._stop_event.is_set()


def test_gateway_tray_start_no_display(icon_file: str) -> None:
    """start() either raises for missing display backends or starts cleanly."""
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    if not _HAS_X11:
        try:
            tray.start()
        except DisplayNameError:
            pass
        else:
            with contextlib.suppress(Exception):
                tray.stop()
    else:
        tray.start()
        with contextlib.suppress(Exception):
            tray.stop()


@pytest.mark.skipif(not _HAS_X11, reason="no X11 display available")
def test_gateway_tray_thread_terminates_on_stop(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.start()

    time.sleep(0.3)

    tray.stop()

    assert tray._thread is not None
    assert not tray._thread.is_alive()
