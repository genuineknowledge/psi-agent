from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from telegram.ext import Application

from psi_agent.channel._types import FileChunk, TextChunk
from psi_agent.channel.telegram import ChannelTelegram, client
from psi_agent.channel.telegram.client import _allowed, _build_chunks, _handle_message, _send_file, run_telegram


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


def _fake_update(text=None, caption=None, photo=None, document=None, user_id=1):
    update = MagicMock()
    update.effective_user.id = user_id
    msg = update.message
    msg.text = text
    msg.caption = caption
    msg.photo = photo
    msg.document = document
    msg.reply_photo = AsyncMock()
    msg.reply_document = AsyncMock()
    msg.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return update


def test_allowed_no_whitelist():
    assert _allowed(1, None) is True


def test_allowed_in_whitelist():
    assert _allowed(1, [1, 2]) is True


def test_allowed_blocked():
    assert _allowed(9, [1, 2]) is False


@pytest.mark.anyio
async def test_handle_message_blocked_by_whitelist():
    update = _fake_update(text="hi", user_id=999)
    context = MagicMock()
    context.bot_data = {"core": MagicMock(), "allowed_ids": [1]}

    await _handle_message(update, context)

    update.message.reply_text.assert_not_called()


@pytest.mark.anyio
async def test_handle_message_streams_and_final_markdown(monkeypatch):
    update = _fake_update(text="hi")
    sent = update.message.reply_text.return_value

    core = MagicMock()

    def _post(_chunks):
        async def _gen():
            yield TextChunk("Hello ")
            yield TextChunk("world")

        return _gen()

    core.post = _post
    context = MagicMock()
    context.bot_data = {"core": core, "allowed_ids": None}

    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    await _handle_message(update, context)

    assert sent.edit_text.await_count >= 1
    final_call = sent.edit_text.call_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "Markdown"
    assert "Hello world" in final_call.args[0]


@pytest.mark.anyio
async def test_handle_message_unsupported(monkeypatch):
    update = _fake_update()
    sent = update.message.reply_text.return_value
    context = MagicMock()
    context.bot_data = {"core": MagicMock(), "allowed_ids": None}

    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[]))

    await _handle_message(update, context)

    sent.edit_text.assert_awaited_with("Unsupported message type")


@pytest.mark.anyio
async def test_handle_message_build_chunks_failure(monkeypatch):
    update = _fake_update(text="hi")
    sent = update.message.reply_text.return_value
    context = MagicMock()
    context.bot_data = {"core": MagicMock(), "allowed_ids": None}

    monkeypatch.setattr(client, "_build_chunks", AsyncMock(side_effect=RuntimeError("boom")))

    await _handle_message(update, context)

    sent.edit_text.assert_awaited_with("Error: boom")


@pytest.mark.anyio
async def test_build_chunks_text(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    update = _fake_update(text="hello world")
    chunks = await _build_chunks(update)
    assert chunks == [TextChunk("hello world")]


@pytest.mark.anyio
async def test_build_chunks_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    update = _fake_update()
    photo = MagicMock()
    photo.file_unique_id = "uid123"
    photo.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))
    update.message.photo = [photo]
    chunks = await _build_chunks(update)
    assert any(isinstance(c, FileChunk) for c in chunks)
    fc = next(c for c in chunks if isinstance(c, FileChunk))
    assert "uid123" in fc.path


@pytest.mark.anyio
async def test_send_file_uses_reply_photo():
    update = MagicMock()
    update.message.reply_photo = AsyncMock()
    update.message.reply_document = AsyncMock()
    await _send_file(update, "/tmp/x.png")
    update.message.reply_photo.assert_awaited_once_with("/tmp/x.png")


@pytest.mark.anyio
async def test_send_file_fallback_to_document():
    update = MagicMock()
    update.message.reply_photo = AsyncMock(side_effect=RuntimeError("not a photo"))
    update.message.reply_document = AsyncMock()
    await _send_file(update, "/tmp/x.bin")
    update.message.reply_document.assert_awaited_once_with("/tmp/x.bin")
