from __future__ import annotations

import multiprocessing

import anyio
import pytest

from psi_agent.gateway._webview import WebViewProcess


def test_webviewprocess_init():
    wv = WebViewProcess("http://127.0.0.1:9999")
    assert wv._url == "http://127.0.0.1:9999"
    assert wv._tray_mode is False
    assert wv._process is None


def test_webviewprocess_init_with_tray():
    wv = WebViewProcess("http://127.0.0.1:9999", tray_mode=True)
    assert wv._tray_mode is True


def test_webviewprocess_is_alive_before_start():
    wv = WebViewProcess("http://127.0.0.1:9999")
    assert wv.is_alive() is False


def test_webviewprocess_double_start_raises():
    wv = WebViewProcess("http://127.0.0.1:9999")

    def fake_main(*args):
        pass

    async def test():
        wv._process = multiprocessing.Process(target=fake_main)
        with pytest.raises(RuntimeError, match="already started"):
            await wv.start()

    anyio.run(test)
