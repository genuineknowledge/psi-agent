from __future__ import annotations

from typing import Any

import pytest

from psi_agent.gateway._webview import GatewayWebView


def test_webview_init():
    wv = GatewayWebView("http://127.0.0.1:9999")
    assert wv._url == "http://127.0.0.1:9999"
    assert wv._has_tray is False
    assert wv._icon is None
    assert wv._on_close is None
    assert wv._window is None


def test_webview_init_with_tray():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    assert wv._has_tray is True


def test_webview_on_closing_no_tray_calls_on_close_and_returns_true():
    called = {"n": 0}

    def _on_close() -> None:
        called["n"] += 1

    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False, on_close=_on_close)
    result = wv._on_closing()
    assert result is True
    assert called["n"] == 1


def test_webview_on_closing_no_tray_without_on_close_returns_true():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False)
    assert wv._on_closing() is True


def test_webview_on_closing_with_tray_hides_and_returns_false():
    calls: list[str] = []

    class _FakeWindow:
        def hide(self) -> None:
            calls.append("hide")

    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    wv._window = _FakeWindow()
    result = wv._on_closing()
    assert result is False
    assert calls == ["hide"]


def test_webview_show_no_window():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.show()  # no window: no-op, must not raise


def test_webview_hide_no_window():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.hide()  # no-op


def test_webview_destroy_no_window():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.destroy()  # no-op


def test_webview_show_hide_destroy_dispatch_to_window():
    calls: list[str] = []

    class _FakeWindow:
        def show(self) -> None:
            calls.append("show")

        def hide(self) -> None:
            calls.append("hide")

        def destroy(self) -> None:
            calls.append("destroy")

    wv = GatewayWebView("http://127.0.0.1:9999")
    wv._window = _FakeWindow()
    wv.show()
    wv.hide()
    wv.destroy()
    assert calls == ["show", "hide", "destroy"]


def test_webview_create_twice_raises():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv._window = object()  # simulate already-created
    with pytest.raises(RuntimeError, match="already created"):
        wv.create()


def test_webview_show_suppresses_window_errors():
    class _BadWindow:
        def show(self, *_: Any) -> None:
            raise RuntimeError("boom")

    wv = GatewayWebView("http://127.0.0.1:9999")
    wv._window = _BadWindow()
    wv.show()  # error is suppressed
