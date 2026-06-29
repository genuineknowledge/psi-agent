from __future__ import annotations

import contextlib
import os
import tempfile
import time

import pytest
from PIL import Image as PILImage
from Xlib.error import DisplayNameError

from psi_agent.gateway._tray import GatewayTray

_HAS_X11 = False
try:
    from Xlib import display as _xdisplay

    _xdisplay.Display()
    _HAS_X11 = True
except DisplayNameError:
    pass


@pytest.fixture
def icon_file():
    img = PILImage.new("RGBA", (64, 64), (41, 98, 255, 255))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f, "PNG")
        path = f.name
    yield path
    os.unlink(path)


def test_gateway_tray_init(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    assert tray._url == "http://127.0.0.1:8888"
    assert tray._icon_path == icon_file
    assert not tray.is_stop_requested()
    assert tray._stop_event is not None


def test_gateway_tray_is_stop_requested(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    assert not tray.is_stop_requested()
    tray._stop_event.set()
    assert tray.is_stop_requested()


def test_gateway_tray_stop_when_not_started(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.stop()


def test_gateway_tray_quit_callback_sets_stop_event(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray._quit()
    assert tray.is_stop_requested()


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


@pytest.mark.skipif(not _HAS_X11, reason="no X11 display available")
def test_gateway_tray_thread_terminates_on_stop(icon_file: str) -> None:
    tray = GatewayTray("http://127.0.0.1:8888", icon_file)
    tray.start()

    time.sleep(0.3)

    tray.stop()

    assert tray._thread is not None
    assert not tray._thread.is_alive()
