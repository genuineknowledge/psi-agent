from __future__ import annotations

import threading

import pytest

from psi_agent.gateway._webview import GatewayWebView


def test_webview_init():
    wv = GatewayWebView("http://127.0.0.1:9999")
    assert wv._url == "http://127.0.0.1:9999"
    assert wv._has_tray is False
    assert wv._icon is None
    assert wv._window is None
    assert wv._thread is None
    assert not wv._closed_event.is_set()


def test_webview_init_with_tray():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    assert wv._has_tray is True


def test_webview_on_closing_no_tray_sets_closed_and_returns_true():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False)
    result = wv._on_closing()
    assert result is True
    assert wv._closed_event.is_set()


def test_webview_on_closing_with_tray_returns_false():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    result = wv._on_closing()
    assert result is False
    assert not wv._closed_event.is_set()


def test_webview_wait_closed_blocks_and_unblocks():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False)
    signal = {"done": False}

    def closer():
        wv._on_closing()
        signal["done"] = True

    t = threading.Thread(target=closer)
    t.start()
    wv.wait_closed()
    t.join()
    assert signal["done"]


def test_webview_stop_when_not_started():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.stop()


def test_webview_is_running_before_start():
    wv = GatewayWebView("http://127.0.0.1:9999")
    assert wv.is_running() is False


def test_webview_double_start_raises():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv._thread = threading.Thread(target=lambda: None)
    with pytest.raises(RuntimeError, match="already started"):
        wv.start()


def test_webview_show_no_window():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.show()
