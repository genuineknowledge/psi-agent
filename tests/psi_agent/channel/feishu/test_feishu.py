from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from lark_channel import PolicyConfig

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, TextChunk
from psi_agent.channel.feishu import ChannelFeishu, client
from psi_agent.channel.feishu.client import (
    _EMOJI_FAILED,
    _EMOJI_PROCESSING,
    _add_reaction,
    _comment_context_header,
    _handle_and_stream,
    _handle_comment,
    _remove_reaction,
    run_feishu,
)


def test_channel_feishu_defaults():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock")
    assert cf.session_socket == "/tmp/feishu.sock"
    assert cf.app_id == ""
    assert cf.app_secret == ""
    assert cf.interval == 1.0
    assert cf.allowed_user_ids is None
    assert cf.require_mention is True
    assert cf.respond_to_mention_all is False
    assert cf.respond_to_comments is True
    assert cf.verbose is False


def test_channel_feishu_with_whitelist():
    cf = ChannelFeishu(
        session_socket="/tmp/feishu.sock",
        app_id="cli_abc",
        app_secret="secret123",
        interval=0.5,
        allowed_user_ids=["ou_123", "ou_456"],
        require_mention=False,
        respond_to_mention_all=True,
        verbose=True,
    )
    assert cf.app_id == "cli_abc"
    assert cf.app_secret == "secret123"
    assert cf.interval == 0.5
    assert cf.allowed_user_ids == ["ou_123", "ou_456"]
    assert cf.require_mention is False
    assert cf.respond_to_mention_all is True
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


@pytest.mark.anyio
async def test_handle_swallows_error_when_notification_also_fails(monkeypatch, tmp_path):
    """_handle_and_stream runs as a start_task_soon task, so it must never propagate —
    even if the error-notification send itself fails — while still flagging CrossMark."""
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(side_effect=RuntimeError("build boom")))

    channel = _fake_channel()
    channel.send = AsyncMock(side_effect=RuntimeError("send boom"))
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_1", message_id="om_1")

    await _handle_and_stream(channel, core, None, ctx)

    acreate = channel.client.im.v1.message_reaction.acreate
    emojis = [c.args[0].request_body.reaction_type.emoji_type for c in acreate.call_args_list]
    assert emojis == [_EMOJI_PROCESSING, _EMOJI_FAILED]
    assert channel.client.im.v1.message_reaction.adelete.call_count == 1


class _FakePortal:
    """Stand-in for anyio BlockingPortal so run_feishu lifecycle tests stay deterministic."""

    async def __aenter__(self) -> _FakePortal:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def start_task_soon(self, *args: object, **kwargs: object) -> None:
        pass


def _patch_feishu(monkeypatch, channel: MagicMock) -> None:
    monkeypatch.setattr(client, "FeishuChannel", lambda **kwargs: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())


@pytest.mark.anyio
async def test_run_feishu_cleans_up_on_startup_failure(monkeypatch):
    """start_background failure must trigger shielded stop_background and re-raise."""
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock(side_effect=RuntimeError("connect boom"))
    channel.stop_background = AsyncMock()
    _patch_feishu(monkeypatch, channel)

    with pytest.raises(RuntimeError, match="connect boom"):
        await run_feishu(session_socket="/tmp/nonexistent.sock", app_id="a", app_secret="s")

    channel.stop_background.assert_awaited()


@pytest.mark.anyio
async def test_run_feishu_cleans_up_on_cancel(monkeypatch):
    """On cancel, stop_background must run under a shielded scope."""
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    _patch_feishu(monkeypatch, channel)

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_feishu, session_socket="/tmp/nonexistent.sock", app_id="a", app_secret="s"))
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    channel.stop_background.assert_awaited()


@pytest.mark.anyio
async def test_run_feishu_passes_policy_to_channel(monkeypatch):
    """run_feishu must build a PolicyConfig and hand it to FeishuChannel."""
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured: dict[str, object] = {}

    def _fake_ctor(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return channel

    monkeypatch.setattr(client, "FeishuChannel", _fake_ctor)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            partial(
                run_feishu,
                session_socket="/tmp/nonexistent.sock",
                app_id="a",
                app_secret="s",
                require_mention=False,
                respond_to_mention_all=True,
            )
        )
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    policy = captured["policy"]
    assert isinstance(policy, PolicyConfig)
    assert policy.require_mention is False
    assert policy.respond_to_mention_all is True
    # message + reject handlers both registered
    registered = {c.args[0] for c in channel.on.call_args_list}
    assert "message" in registered
    assert "reject" in registered


@pytest.mark.anyio
async def test_run_feishu_defaults_require_mention(monkeypatch):
    """Default policy: require_mention True, respond_to_mention_all False."""
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured: dict[str, object] = {}
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: captured.update(kw) or channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_feishu, session_socket="/tmp/x.sock", app_id="a", app_secret="s"))
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    policy = captured["policy"]
    assert isinstance(policy, PolicyConfig)
    assert policy.require_mention is True
    assert policy.respond_to_mention_all is False


@pytest.mark.anyio
async def test_ensure_bot_identity_uses_cached_identity():
    channel = MagicMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")
    channel.resolve_bot_identity = AsyncMock()
    await client._ensure_bot_identity(channel)
    channel.resolve_bot_identity.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_bot_identity_resolves_when_missing():
    channel = MagicMock()
    channel.bot_identity = None
    channel.resolve_bot_identity = AsyncMock(return_value=SimpleNamespace(open_id="ou_bot", name="Haitun"))
    await client._ensure_bot_identity(channel)
    channel.resolve_bot_identity.assert_awaited_once()


@pytest.mark.anyio
async def test_ensure_bot_identity_warns_when_unresolved(caplog):
    channel = MagicMock()
    channel.bot_identity = None
    channel.resolve_bot_identity = AsyncMock(return_value=None)
    # Must not raise even though group @-detection will be unavailable.
    await client._ensure_bot_identity(channel)
    channel.resolve_bot_identity.assert_awaited_once()


@pytest.mark.anyio
async def test_ensure_bot_identity_swallows_resolve_error():
    channel = MagicMock()
    channel.bot_identity = None
    channel.resolve_bot_identity = AsyncMock(side_effect=RuntimeError("boom"))
    # Startup must survive a failing identity lookup.
    await client._ensure_bot_identity(channel)


def test_log_reject_swallows_and_reads_fields():
    # Should not raise on a well-formed event nor on a broken one.
    client._log_reject(SimpleNamespace(message_id="om_1", reason="policy_no_mention"))
    client._log_reject(object())


@pytest.mark.anyio
async def test_build_chunks_text_only(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    channel = _fake_channel()
    ctx = SimpleNamespace(
        content_text="hello world",
        message_id="om_1",
        chat_id="oc_1",
        chat_type="p2p",
        sender_id="ou_1",
        resources=[],
        raw_content_type="text",
    )
    chunks = await client._build_chunks(channel, ctx)
    # First chunk is the feishu metadata header, then the message text.
    assert len(chunks) == 2
    assert isinstance(chunks[0], TextChunk)
    assert "chat_id: oc_1" in chunks[0].text
    assert chunks[1] == TextChunk("hello world")


@pytest.mark.anyio
async def test_build_chunks_group_header_carries_chat_id(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    channel = _fake_channel()
    ctx = SimpleNamespace(
        content_text="看看这个",
        message_id="om_2",
        chat_id="oc_group",
        chat_type="group",
        sender_id="ou_9",
        resources=[],
        raw_content_type="text",
    )
    chunks = await client._build_chunks(channel, ctx)
    header = chunks[0]
    assert isinstance(header, TextChunk)
    assert "chat_type: group" in header.text
    assert "chat_id: oc_group" in header.text
    # channel 层保持与 workspace 工具解耦: header 不含具体工具名
    assert "feishu_message_list" not in header.text


@pytest.mark.anyio
async def test_build_chunks_empty_returns_no_chunks(monkeypatch, tmp_path):
    """No text/audio/resource -> header dropped, empty list (unsupported type)."""
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    channel = _fake_channel()
    ctx = SimpleNamespace(
        content_text="",
        message_id="om_3",
        chat_id="oc_1",
        chat_type="p2p",
        sender_id="ou_1",
        resources=[],
        raw_content_type="unknown",
    )
    chunks = await client._build_chunks(channel, ctx)
    assert chunks == []


@pytest.mark.anyio
async def test_build_chunks_with_resource(monkeypatch, tmp_path):
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    channel = _fake_channel()
    channel.download_resource_to_file = AsyncMock(return_value=str(tmp_path / "file.bin"))
    resource = SimpleNamespace(type="file", file_key="fk_1", file_name="file.bin")
    ctx = SimpleNamespace(content_text="", message_id="om_1", resources=[resource], raw_content_type="file")
    chunks = await client._build_chunks(channel, ctx)
    assert any(isinstance(c, FileChunk) for c in chunks)
    channel.download_resource_to_file.assert_awaited_once()


# --------------------------------------------------------------------------
# Document comment handling (@bot in doc comments -> reply on the comment)
# --------------------------------------------------------------------------


def _comment_event(*, mentioned_bot=True, operator_open_id="ou_1", reply_id="re_1") -> SimpleNamespace:
    return SimpleNamespace(
        file_token="doccnXXX",
        file_type="docx",
        comment_id="cmt_1",
        reply_id=reply_id,
        operator=SimpleNamespace(open_id=operator_open_id, user_id="u_1", union_id="on_1"),
        mentioned_bot=mentioned_bot,
    )


def _comment_channel(*, supported=True) -> MagicMock:
    channel = MagicMock()
    target = SimpleNamespace(file_token="doccnXXX", file_type="docx", supported=supported, reason=None)
    ctx = SimpleNamespace(
        target=target,
        comment_id="cmt_1",
        question="机器人这段怎么改?",
        quote="原文片段",
        is_whole=False,
        target_reply_id="re_1",
    )
    channel.resolve_comment_target = AsyncMock(return_value=target)
    channel.get_comment_context = AsyncMock(return_value=ctx)
    channel.reply_comment = AsyncMock()
    return channel


def test_comment_context_header_has_facts_no_tool_names():
    event = _comment_event()
    ctx = SimpleNamespace(quote="原文片段")
    header = _comment_context_header(event, ctx)
    assert "file_token: doccnXXX" in header
    assert "file_type: docx" in header
    assert "comment_id: cmt_1" in header
    assert "operator_open_id: ou_1" in header
    assert "quote: 原文片段" in header
    # channel 层与 workspace 工具解耦: header 不含具体工具名
    assert "feishu" not in header.replace("feishu_comment_context", "")


@pytest.mark.anyio
async def test_handle_comment_replies_with_agent_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="改成这样"))
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, core, None, _comment_event())

    channel.resolve_comment_target.assert_awaited_once()
    channel.get_comment_context.assert_awaited_once()
    # event_reply_id 透传, 使回复挂到被@的那条 reply 上
    assert channel.get_comment_context.call_args.kwargs["event_reply_id"] == "re_1"
    channel.reply_comment.assert_awaited_once()
    assert channel.reply_comment.call_args.args[1] == "改成这样"


@pytest.mark.anyio
async def test_handle_comment_skips_when_not_mentioned(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, core, None, _comment_event(mentioned_bot=False))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_respects_whitelist(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, core, ["ou_allowed"], _comment_event(operator_open_id="ou_blocked"))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_skips_unsupported_target(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="x"))
    channel = _comment_channel(supported=False)
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, core, None, _comment_event())

    channel.get_comment_context.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_replies_error_on_agent_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(side_effect=RuntimeError("agent boom")))
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, core, None, _comment_event())

    channel.reply_comment.assert_awaited_once()
    assert "agent boom" in channel.reply_comment.call_args.args[1]


@pytest.mark.anyio
async def test_handle_comment_swallows_reply_error(monkeypatch, tmp_path):
    """_handle_comment runs as a start_task_soon task, so it must never propagate."""
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="ok"))
    channel = _comment_channel()
    channel.reply_comment = AsyncMock(side_effect=RuntimeError("reply boom"))
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    # Must not raise.
    await _handle_comment(channel, core, None, _comment_event())


@pytest.mark.anyio
async def test_run_feishu_registers_comment_when_enabled(monkeypatch):
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_feishu, session_socket="/tmp/x.sock", app_id="a", app_secret="s"))
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    registered = {c.args[0] for c in channel.on.call_args_list}
    assert "comment" in registered


@pytest.mark.anyio
async def test_run_feishu_skips_comment_when_disabled(monkeypatch):
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            partial(
                run_feishu,
                session_socket="/tmp/x.sock",
                app_id="a",
                app_secret="s",
                respond_to_comments=False,
            )
        )
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    registered = {c.args[0] for c in channel.on.call_args_list}
    assert "comment" not in registered
    # message/reject 仍然注册
    assert "message" in registered
