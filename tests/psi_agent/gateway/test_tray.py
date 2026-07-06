from __future__ import annotations

import contextlib
import time
from pathlib import Path

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
