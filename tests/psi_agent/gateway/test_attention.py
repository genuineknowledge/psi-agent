from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from PIL import Image

from psi_agent.gateway._attention import AttentionHub, _make_highlight_image


@pytest.mark.anyio
async def test_attention_hub_notify_calls_bound_targets() -> None:
    hub = AttentionHub()
    tray = MagicMock()
    webview = MagicMock()
    webview.send = AsyncMock()
    hub.bind(tray=tray, webview=webview)

    await hub.notify()
    # schedule_notify runs notify_sync on a daemon thread → from_thread.run schedules in event loop
    await anyio.sleep(0.05)

    tray.request_attention.assert_called_once()
    webview.send.assert_called_once_with("flash")


@pytest.mark.anyio
async def test_schedule_notify_is_non_blocking() -> None:
    hub = AttentionHub()
    tray = MagicMock()
    tray.request_attention.side_effect = lambda: time.sleep(0.2)
    hub.bind(tray=tray)

    t0 = time.perf_counter()
    hub.schedule_notify()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05
    await anyio.sleep(0.25)
    tray.request_attention.assert_called_once()


def test_make_highlight_image_preserves_size() -> None:
    base = Image.new("RGBA", (32, 32), (41, 98, 255, 255))
    highlight = _make_highlight_image(base)
    assert highlight.size == base.size
    assert highlight.mode == "RGBA"
