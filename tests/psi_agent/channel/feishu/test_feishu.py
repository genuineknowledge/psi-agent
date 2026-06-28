from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk
from psi_agent.channel.feishu import ChannelFeishu, client
from psi_agent.channel.feishu.client import (
    _EMOJI_FAILED,
    _EMOJI_PROCESSING,
    _add_reaction,
    _handle_and_stream,
    _remove_reaction,
)


def test_channel_feishu_defaults():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock")
    assert cf.session_socket == "/tmp/feishu.sock"
    assert cf.app_id == ""
    assert cf.app_secret == ""
    assert cf.interval == 1.0
    assert cf.allowed_user_ids is None
    assert cf.verbose is False


def test_channel_feishu_with_whitelist():
    cf = ChannelFeishu(
        session_socket="/tmp/feishu.sock",
        app_id="cli_abc",
        app_secret="secret123",
        interval=0.5,
        allowed_user_ids=["ou_123", "ou_456"],
        verbose=True,
    )
    assert cf.app_id == "cli_abc"
    assert cf.app_secret == "secret123"
    assert cf.interval == 0.5
    assert cf.allowed_user_ids == ["ou_123", "ou_456"]
    assert cf.verbose is True


@pytest.mark.anyio
async def test_run_raises_on_missing_app_id():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock", app_secret="secret")
    with pytest.raises(ValueError, match="app_id"):
        await cf.run()


@pytest.mark.anyio
async def test_run_raises_on_missing_app_secret():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock", app_id="cli_abc")
    with pytest.raises(ValueError, match="app_secret"):
        await cf.run()


def _fake_channel() -> MagicMock:
    channel = MagicMock()
    channel.client.im.v1.message_reaction.acreate = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(reaction_id="rid_1"))
    )
    channel.client.im.v1.message_reaction.adelete = AsyncMock()
    channel.send = AsyncMock()
    channel.stream = AsyncMock()
    return channel


@pytest.mark.anyio
async def test_add_reaction_returns_reaction_id():
    channel = _fake_channel()
    rid = await _add_reaction(channel, "om_1", _EMOJI_PROCESSING)
    assert rid == "rid_1"
    req = channel.client.im.v1.message_reaction.acreate.call_args.args[0]
    assert req.message_id == "om_1"
    assert req.request_body.reaction_type.emoji_type == "Typing"


@pytest.mark.anyio
async def test_add_reaction_returns_none_on_error():
    channel = _fake_channel()
    channel.client.im.v1.message_reaction.acreate = AsyncMock(side_effect=RuntimeError("boom"))
    rid = await _add_reaction(channel, "om_1", _EMOJI_PROCESSING)
    assert rid is None


@pytest.mark.anyio
async def test_remove_reaction_calls_adelete():
    channel = _fake_channel()
    await _remove_reaction(channel, "om_1", "rid_1")
    req = channel.client.im.v1.message_reaction.adelete.call_args.args[0]
    assert req.message_id == "om_1"
    assert req.reaction_id == "rid_1"


@pytest.mark.anyio
async def test_remove_reaction_swallows_error():
    channel = _fake_channel()
    channel.client.im.v1.message_reaction.adelete = AsyncMock(side_effect=RuntimeError("boom"))
    await _remove_reaction(channel, "om_1", "rid_1")


@pytest.mark.anyio
async def test_handle_success_removes_typing_no_crossmark(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    channel = _fake_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_1", message_id="om_1")

    await _handle_and_stream(channel, core, None, ctx)

    acreate = channel.client.im.v1.message_reaction.acreate
    adelete = channel.client.im.v1.message_reaction.adelete
    assert acreate.call_count == 1
    assert acreate.call_args_list[0].args[0].request_body.reaction_type.emoji_type == _EMOJI_PROCESSING
    assert adelete.call_count == 1
    assert adelete.call_args_list[0].args[0].reaction_id == "rid_1"


@pytest.mark.anyio
async def test_handle_failure_replaces_with_crossmark(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    channel = _fake_channel()
    channel.stream = AsyncMock(side_effect=RuntimeError("stream boom"))
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_1", message_id="om_1")

    await _handle_and_stream(channel, core, None, ctx)

    acreate = channel.client.im.v1.message_reaction.acreate
    adelete = channel.client.im.v1.message_reaction.adelete
    emojis = [c.args[0].request_body.reaction_type.emoji_type for c in acreate.call_args_list]
    assert emojis == [_EMOJI_PROCESSING, _EMOJI_FAILED]
    assert adelete.call_count == 1
    channel.send.assert_awaited()
