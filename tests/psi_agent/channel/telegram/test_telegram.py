from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from telegram.ext import Application

from psi_agent.channel.telegram import ChannelTelegram
from psi_agent.channel.telegram.client import run_telegram


def test_channel_telegram_defaults():
    ct = ChannelTelegram(session_socket="/tmp/chan.sock")
    assert ct.session_socket == "/tmp/chan.sock"
    assert ct.bot_token == ""
    assert ct.interval == 1.0
    assert ct.allowed_user_ids is None
    assert ct.verbose is False


def test_channel_telegram_with_whitelist():
    ct = ChannelTelegram(
        session_socket="/tmp/chan.sock",
        bot_token="abc",
        interval=0.5,
        allowed_user_ids=[123, 456],
        verbose=True,
    )
    assert ct.bot_token == "abc"
    assert ct.interval == 0.5
    assert ct.allowed_user_ids == [123, 456]
    assert ct.verbose is True


@pytest.mark.anyio
async def test_run_raises_on_missing_token():
    ct = ChannelTelegram(session_socket="/tmp/chan.sock")
    with pytest.raises(ValueError, match="No Telegram bot token"):
        await ct.run()


def _mock_app() -> MagicMock:
    app = MagicMock(spec=Application)
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.updater = MagicMock()
    app.updater.start_polling = AsyncMock()
    app.updater.stop = AsyncMock()
    return app


def _patch_builder(monkeypatch, app: MagicMock) -> None:
    builder = MagicMock()
    builder.token.return_value = builder
    builder.proxy.return_value = builder
    builder.get_updates_proxy.return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(Application, "builder", lambda *a, **k: builder)


@pytest.mark.anyio
async def test_run_telegram_cleans_up_on_startup_failure(monkeypatch):
    """A startup failure (start_polling) must trigger shielded teardown and re-raise."""
    app = _mock_app()
    app.updater.start_polling = AsyncMock(side_effect=RuntimeError("polling boom"))
    _patch_builder(monkeypatch, app)

    with pytest.raises(RuntimeError, match="polling boom"):
        await run_telegram(session_socket="/tmp/nonexistent.sock", bot_token="t")

    app.start.assert_awaited()
    app.stop.assert_awaited()
    app.shutdown.assert_awaited()


@pytest.mark.anyio
async def test_run_telegram_cleans_up_on_cancel(monkeypatch):
    """On cancel during polling, teardown must run under a shielded scope."""
    app = _mock_app()
    _patch_builder(monkeypatch, app)

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_telegram, session_socket="/tmp/nonexistent.sock", bot_token="t"))
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    app.updater.stop.assert_awaited()
    app.stop.assert_awaited()
    app.shutdown.assert_awaited()
