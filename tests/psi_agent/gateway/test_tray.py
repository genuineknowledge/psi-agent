from __future__ import annotations

import contextlib
import time

import pytest

from psi_agent.gateway._tray import _HAS_PYSTRAY, GatewayTray, _create_icon_image


@pytest.mark.skipif(not _HAS_PYSTRAY, reason="pystray not available")
def test_create_icon_image_returns_pil_image() -> None:
    img = _create_icon_image()
    assert img is not None
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_gateway_tray_init() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    assert tray._url == "http://127.0.0.1:8888"
    assert not tray.is_stop_requested()
    assert tray._stop_event is not None


def test_gateway_tray_is_stop_requested() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    assert not tray.is_stop_requested()
    tray._stop_event.set()
    assert tray.is_stop_requested()


def test_gateway_tray_stop_when_not_started() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.stop()


def test_gateway_tray_quit_callback_sets_stop_event() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    tray._quit()
    assert tray.is_stop_requested()


def test_gateway_tray_start_no_display() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.start()
    with contextlib.suppress(Exception):
        tray.stop()


@pytest.mark.skipif(not _HAS_PYSTRAY, reason="pystray not available")
def test_gateway_tray_thread_terminates_on_stop() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.start()

    time.sleep(0.3)

    tray.stop()

    assert tray._thread is not None
    assert not tray._thread.is_alive()
