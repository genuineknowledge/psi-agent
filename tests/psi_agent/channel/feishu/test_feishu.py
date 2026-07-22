from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from functools import partial
from types import SimpleNamespace
from typing import Any, cast
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
    _CoreRegistry,
    _derive_socket,
    _GatewaySessionProvider,
    _handle_and_stream,
    _handle_comment,
    _remove_reaction,
    _sanitize_open_id,
    run_feishu,
)


def _const_resolver(core: ChannelCore):
    """把单个 core 包成 _handle_and_stream 期望的 async resolver。"""

    async def _resolve(_open_id: str | None) -> ChannelCore:
        return core

    return _resolve


def test_channel_feishu_defaults():
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock")
    assert cf.session_socket == "/tmp/feishu.sock"
    assert cf.route_template is None
    assert cf.gateway_url is None
    assert cf.ai_id == ""
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

    await _handle_and_stream(channel, _const_resolver(core), None, ctx)

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

    await _handle_and_stream(channel, _const_resolver(core), None, ctx)

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

    await _handle_and_stream(channel, _const_resolver(core), None, ctx)

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

    await _handle_comment(channel, _const_resolver(core), None, _comment_event())

    channel.resolve_comment_target.assert_awaited_once()
    channel.get_comment_context.assert_awaited_once()
    channel.reply_comment.assert_awaited_once()
    assert channel.reply_comment.call_args.args[1] == "改成这样"
    # 数据安全: 回复前强制 is_whole=True, 使 SDK 走 POST 新建评论而非
    # PUT 覆盖用户那条 @机器人 的 reply(否则会抹掉用户原评论)
    replied_ctx = channel.reply_comment.call_args.args[0]
    assert replied_ctx.is_whole is True


@pytest.mark.anyio
async def test_handle_comment_never_overwrites_user_reply(monkeypatch, tmp_path):
    """回归: 即便 get_comment_context 返回 is_whole=False(锚定评论),
    _handle_comment 也必须把 ctx.is_whole 置 True 再回复, 确保 SDK 走
    新建评论(POST)而非覆盖 reply(PUT)。"""
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="answer"))
    channel = _comment_channel()  # ctx.is_whole 默认 False
    assert channel.get_comment_context.return_value.is_whole is False
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _const_resolver(core), None, _comment_event())

    replied_ctx = channel.reply_comment.call_args.args[0]
    assert replied_ctx.is_whole is True


@pytest.mark.anyio
async def test_handle_comment_skips_when_not_mentioned(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _const_resolver(core), None, _comment_event(mentioned_bot=False))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_respects_whitelist(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _const_resolver(core), ["ou_allowed"], _comment_event(operator_open_id="ou_blocked"))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_skips_unsupported_target(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="x"))
    channel = _comment_channel(supported=False)
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _const_resolver(core), None, _comment_event())

    channel.get_comment_context.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_replies_error_on_agent_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(side_effect=RuntimeError("agent boom")))
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _const_resolver(core), None, _comment_event())

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
    await _handle_comment(channel, _const_resolver(core), None, _comment_event())


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


# ---- per-user socket routing ----


def test_sanitize_open_id_identity_for_safe_ids():
    # 飞书 open_id 字符集 [A-Za-z0-9_], 净化是恒等变换
    assert _sanitize_open_id("ou_abc123") == "ou_abc123"


def test_sanitize_open_id_replaces_unsafe_chars():
    assert _sanitize_open_id("ou/ab c:d\\e") == "ou_ab_c_d_e"


def test_derive_socket_unix_template():
    assert (
        _derive_socket("ou_1", route_template="/tmp/psi/session/{open_id}.sock", fallback="/tmp/shared.sock")
        == "/tmp/psi/session/ou_1.sock"
    )


def test_derive_socket_windows_pipe_template():
    assert (
        _derive_socket("ou_1", route_template=r"\\.\pipe\psi\session\{open_id}", fallback="/tmp/shared.sock")
        == r"\\.\pipe\psi\session\ou_1"
    )


def test_derive_socket_tcp_template():
    assert (
        _derive_socket("ou_1", route_template="http://127.0.0.1:9000/{open_id}", fallback="/tmp/shared.sock")
        == "http://127.0.0.1:9000/ou_1"
    )


def test_derive_socket_no_template_falls_back():
    assert _derive_socket("ou_1", route_template=None, fallback="/tmp/shared.sock") == "/tmp/shared.sock"


def test_derive_socket_empty_open_id_falls_back():
    assert (
        _derive_socket("", route_template="/tmp/psi/session/{open_id}.sock", fallback="/tmp/shared.sock")
        == "/tmp/shared.sock"
    )


def test_derive_socket_none_open_id_falls_back():
    # lark message context sender_id 是 Optional[str], None 时回退共享 socket
    assert (
        _derive_socket(None, route_template="/tmp/psi/session/{open_id}.sock", fallback="/tmp/shared.sock")
        == "/tmp/shared.sock"
    )


def test_derive_socket_sanitizes_unsafe_open_id():
    assert (
        _derive_socket("ou/x", route_template="/tmp/psi/session/{open_id}.sock", fallback="/tmp/shared.sock")
        == "/tmp/psi/session/ou_x.sock"
    )


@pytest.mark.anyio
async def test_core_registry_reuses_and_isolates(tmp_path):
    async with AsyncExitStack() as stack:
        reg = _CoreRegistry(1.0, stack)
        a1 = await reg.get(str(tmp_path / "a.sock"))
        a2 = await reg.get(str(tmp_path / "a.sock"))
        b = await reg.get(str(tmp_path / "b.sock"))
        assert a1 is a2  # 同 socket 复用
        assert a1 is not b  # 不同 socket 隔离


@pytest.mark.anyio
async def test_core_registry_concurrent_get_creates_one(tmp_path):
    """并发 get 同一 socket 只建一个实例(验证 double-checked 锁)。"""
    socket = str(tmp_path / "a.sock")
    results: list[ChannelCore] = []
    async with AsyncExitStack() as stack:
        reg = _CoreRegistry(1.0, stack)

        async def _grab() -> None:
            results.append(await reg.get(socket))

        async with anyio.create_task_group() as tg:
            for _ in range(5):
                tg.start_soon(_grab)

        assert len({id(c) for c in results}) == 1


@pytest.mark.anyio
async def test_core_registry_closes_all_on_exit(tmp_path):
    async with AsyncExitStack() as stack:
        reg = _CoreRegistry(1.0, stack)
        core = await reg.get(str(tmp_path / "a.sock"))
    # stack 退出后 ChannelCore.__aexit__ 已关闭 aiohttp session
    assert core._session.closed is True


@pytest.mark.anyio
async def test_handle_skips_resolver_for_blocked_sender():
    """白名单校验在解析 core 之前, 被拦用户绝不触发 core 创建。"""
    channel = _fake_channel()

    async def _boom(_open_id: str | None) -> ChannelCore:
        raise AssertionError("resolver must not be called for blocked sender")

    ctx = SimpleNamespace(sender_id="ou_bad", chat_id="oc_1", message_id="om_1")
    await _handle_and_stream(channel, _boom, ["ou_good"], ctx)
    channel.client.im.v1.message_reaction.acreate.assert_not_awaited()


@pytest.mark.anyio
async def test_run_feishu_routes_distinct_senders_to_distinct_cores(monkeypatch, tmp_path):
    """route_template 生效时, 不同 open_id 经 resolve_core 拿到不同 socket 的 core;
    同一 open_id 复用同一 core。捕获 run_feishu 注册的 _on_message 直接驱动。"""
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured_handler: dict[str, Callable[[Any], Awaitable[None]]] = {}
    resolved: list[Callable[[str | None], Awaitable[ChannelCore]]] = []

    def _on(event: str, cb: Callable[[Any], Awaitable[None]]) -> None:
        if event == "message":
            captured_handler["cb"] = cb

    channel.on = MagicMock(side_effect=_on)

    class _CapturePortal(_FakePortal):
        def start_task_soon(self, *args: object, **kwargs: object) -> None:
            resolved.append(cast("Callable[[str | None], Awaitable[ChannelCore]]", args[2]))

    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _CapturePortal())

    template = str(tmp_path / "session" / "{open_id}.sock")
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            partial(
                run_feishu,
                session_socket=str(tmp_path / "shared.sock"),
                app_id="a",
                app_secret="s",
                route_template=template,
            )
        )
        await anyio.sleep(0.1)
        cb = captured_handler["cb"]
        await cb(SimpleNamespace(sender_id="ou_1"))
        await cb(SimpleNamespace(sender_id="ou_2"))
        await cb(SimpleNamespace(sender_id="ou_1"))
        resolve_core = resolved[0]
        c1 = await resolve_core("ou_1")
        c2 = await resolve_core("ou_2")
        c1b = await resolve_core("ou_1")
        tg.cancel_scope.cancel()

    assert c1.session_socket == str(tmp_path / "session" / "ou_1.sock")
    assert c2.session_socket == str(tmp_path / "session" / "ou_2.sock")
    assert c1 is c1b
    assert c1 is not c2


@pytest.mark.anyio
async def test_run_feishu_no_template_shares_single_core(monkeypatch, tmp_path):
    """route_template=None 时任意 sender 都路由到共享 session_socket。"""
    channel = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured_handler: dict[str, Callable[[Any], Awaitable[None]]] = {}
    resolved: list[Callable[[str | None], Awaitable[ChannelCore]]] = []

    def _on(event: str, cb: Callable[[Any], Awaitable[None]]) -> None:
        if event == "message":
            captured_handler["cb"] = cb

    channel.on = MagicMock(side_effect=_on)

    class _CapturePortal(_FakePortal):
        def start_task_soon(self, *args: object, **kwargs: object) -> None:
            resolved.append(cast("Callable[[str | None], Awaitable[ChannelCore]]", args[2]))

    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _CapturePortal())

    shared = str(tmp_path / "shared.sock")
    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_feishu, session_socket=shared, app_id="a", app_secret="s"))
        await anyio.sleep(0.1)
        cb = captured_handler["cb"]
        await cb(SimpleNamespace(sender_id="ou_1"))
        resolve_core = resolved[0]
        c1 = await resolve_core("ou_1")
        c2 = await resolve_core("ou_2")
        tg.cancel_scope.cancel()

    assert c1.session_socket == shared
    assert c1 is c2


# ---- Gateway session auto-provisioning ----


class _FakeResp:
    """最小 aiohttp 响应替身: 支持 async with, .status, .json(), .text()。"""

    def __init__(self, status: int, *, json_body: Any = None, text_body: str = "") -> None:
        self.status = status
        self._json = json_body
        self._text = text_body

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._json

    async def text(self) -> str:
        return self._text


class _FakeHttp:
    """记录 POST/GET 调用的 aiohttp.ClientSession 替身。

    ``post_responses`` / ``get_responses`` 是按调用次序弹出的响应队列。
    """

    def __init__(self, post_responses: list[_FakeResp], get_responses: list[_FakeResp] | None = None) -> None:
        self._posts = list(post_responses)
        self._gets = list(get_responses or [])
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    def post(self, url: str, *, json: Any = None, timeout: Any = None) -> _FakeResp:
        self.post_calls.append({"url": url, "json": json})
        return self._posts.pop(0)

    def get(self, url: str, *, timeout: Any = None) -> _FakeResp:
        self.get_calls.append(url)
        return self._gets.pop(0)

    async def __aenter__(self) -> _FakeHttp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _provider(http: _FakeHttp, ai_id: str = "ai-1", base: str = "http://gw") -> _GatewaySessionProvider:
    return _GatewaySessionProvider(base, ai_id, cast("Any", http))


def _patch_gateway_http(monkeypatch: Any, http: _FakeHttp) -> None:
    """把 run_feishu 里的 aiohttp.ClientSession() 换成假 gateway http。

    run_feishu 用无参 ``aiohttp.ClientSession()`` 建 REST 客户端, 而 ChannelCore 用
    带 ``connector=`` 的调用建自己的 session —— 二者共用同一个 ``aiohttp.ClientSession``
    符号。这里按是否传 ``connector`` 区分: 无 connector 返回假 gateway http, 有则委托
    真实类, 避免污染 ChannelCore 的懒连接。
    """
    real = client.aiohttp.ClientSession

    def _factory(*args: Any, **kwargs: Any) -> Any:
        if "connector" in kwargs:
            return real(*args, **kwargs)
        return http

    monkeypatch.setattr(client.aiohttp, "ClientSession", _factory)


@pytest.mark.anyio
async def test_provider_new_open_id_posts_and_caches():
    http = _FakeHttp([_FakeResp(201, json_body={"id": "ou_1", "channel_socket": "/tmp/ch/ou_1.sock"})])
    prov = _provider(http)
    sock = await prov.ensure("ou_1")
    assert sock == "/tmp/ch/ou_1.sock"
    # 第二次命中缓存, 不再 POST
    sock2 = await prov.ensure("ou_1")
    assert sock2 == "/tmp/ch/ou_1.sock"
    assert len(http.post_calls) == 1
    assert http.post_calls[0]["json"] == {"ai_id": "ai-1", "id": "ou_1"}


@pytest.mark.anyio
async def test_provider_already_exists_fetches_via_get():
    http = _FakeHttp(
        post_responses=[_FakeResp(400, text_body='{"error": "Session \'ou_1\' already exists"}')],
        get_responses=[
            _FakeResp(
                200,
                json_body=[
                    {"id": "other", "channel_socket": "/x"},
                    {"id": "ou_1", "channel_socket": "/tmp/ch/ou_1.sock"},
                ],
            )
        ],
    )
    prov = _provider(http)
    sock = await prov.ensure("ou_1")
    assert sock == "/tmp/ch/ou_1.sock"
    assert len(http.get_calls) == 1


@pytest.mark.anyio
async def test_provider_bad_request_raises():
    # 400 但非 already-exists (如 ai_id 缺失) → 抛, 由调用方回退
    http = _FakeHttp([_FakeResp(400, text_body='{"error": "missing ai_id"}')])
    prov = _provider(http)
    with pytest.raises(RuntimeError, match="POST /sessions failed"):
        await prov.ensure("ou_1")


@pytest.mark.anyio
async def test_provider_unknown_ai_id_raises():
    http = _FakeHttp([_FakeResp(404, text_body='{"error": "AI not found"}')])
    prov = _provider(http)
    with pytest.raises(RuntimeError, match="POST /sessions failed"):
        await prov.ensure("ou_1")


@pytest.mark.anyio
async def test_provider_not_found_in_get_raises():
    http = _FakeHttp(
        post_responses=[_FakeResp(400, text_body="already exists")],
        get_responses=[_FakeResp(200, json_body=[{"id": "other", "channel_socket": "/x"}])],
    )
    prov = _provider(http)
    with pytest.raises(RuntimeError, match="not found"):
        await prov.ensure("ou_1")


@pytest.mark.anyio
async def test_provider_failure_not_cached():
    # 首次失败不写缓存, 下条消息重试 gateway (第二次给成功响应)
    http = _FakeHttp(
        [
            _FakeResp(500, text_body="boom"),
            _FakeResp(201, json_body={"id": "ou_1", "channel_socket": "/tmp/ch/ou_1.sock"}),
        ]
    )
    prov = _provider(http)
    with pytest.raises(RuntimeError):
        await prov.ensure("ou_1")
    sock = await prov.ensure("ou_1")
    assert sock == "/tmp/ch/ou_1.sock"
    assert len(http.post_calls) == 2


@pytest.mark.anyio
async def test_provider_sanitizes_open_id_in_request():
    http = _FakeHttp([_FakeResp(201, json_body={"id": "ou_x", "channel_socket": "/s"})])
    prov = _provider(http)
    await prov.ensure("ou/x")
    assert http.post_calls[0]["json"]["id"] == "ou_x"


@pytest.mark.anyio
async def test_provider_concurrent_same_open_id_posts_once():
    http = _FakeHttp([_FakeResp(201, json_body={"id": "ou_1", "channel_socket": "/tmp/ch/ou_1.sock"})])
    prov = _provider(http)
    results: list[str] = []

    async def _grab() -> None:
        results.append(await prov.ensure("ou_1"))

    async with anyio.create_task_group() as tg:
        for _ in range(5):
            tg.start_soon(_grab)

    assert len(http.post_calls) == 1
    assert set(results) == {"/tmp/ch/ou_1.sock"}


@pytest.mark.anyio
async def test_run_raises_when_gateway_url_without_ai_id(monkeypatch):
    monkeypatch.delenv("PSI_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("PSI_FEISHU_APP_SECRET", raising=False)
    cf = ChannelFeishu(session_socket="/tmp/feishu.sock", app_id="a", app_secret="s", gateway_url="http://gw")
    with pytest.raises(ValueError, match="ai_id"):
        await cf.run()


@pytest.mark.anyio
async def test_run_feishu_gateway_mode_provisions_per_user_socket(monkeypatch, tmp_path):
    """gateway 模式: resolve_core 经 Gateway 为每个 open_id 拿回独立 channel_socket。"""
    channel = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured_handler: dict[str, Callable[[Any], Awaitable[None]]] = {}
    resolved: list[Callable[[str | None], Awaitable[ChannelCore]]] = []

    def _on(event: str, cb: Callable[[Any], Awaitable[None]]) -> None:
        if event == "message":
            captured_handler["cb"] = cb

    channel.on = MagicMock(side_effect=_on)

    class _CapturePortal(_FakePortal):
        def start_task_soon(self, *args: object, **kwargs: object) -> None:
            resolved.append(cast("Callable[[str | None], Awaitable[ChannelCore]]", args[2]))

    a = str(tmp_path / "ou_1.sock")
    b = str(tmp_path / "ou_2.sock")
    http = _FakeHttp(
        [
            _FakeResp(201, json_body={"id": "ou_1", "channel_socket": a}),
            _FakeResp(201, json_body={"id": "ou_2", "channel_socket": b}),
        ]
    )
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _CapturePortal())
    _patch_gateway_http(monkeypatch, http)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            partial(
                run_feishu,
                session_socket=str(tmp_path / "shared.sock"),
                app_id="a",
                app_secret="s",
                gateway_url="http://gw",
                ai_id="ai-1",
            )
        )
        await anyio.sleep(0.1)
        resolve_core = resolved[0] if resolved else None
        assert resolve_core is None  # 尚无消息, 未捕获 resolver
        cb = captured_handler["cb"]
        await cb(SimpleNamespace(sender_id="ou_1"))
        resolve_core = resolved[0]
        c1 = await resolve_core("ou_1")
        c2 = await resolve_core("ou_2")
        c1b = await resolve_core("ou_1")
        tg.cancel_scope.cancel()

    assert c1.session_socket == a
    assert c2.session_socket == b
    assert c1 is c1b
    assert c1 is not c2


@pytest.mark.anyio
async def test_run_feishu_gateway_unreachable_falls_back_to_shared(monkeypatch, tmp_path):
    """Gateway 创建失败时 resolve_core 回退共享 session_socket, 用户仍有回复。"""
    channel = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")

    captured_handler: dict[str, Callable[[Any], Awaitable[None]]] = {}
    resolved: list[Callable[[str | None], Awaitable[ChannelCore]]] = []

    def _on(event: str, cb: Callable[[Any], Awaitable[None]]) -> None:
        if event == "message":
            captured_handler["cb"] = cb

    channel.on = MagicMock(side_effect=_on)

    class _CapturePortal(_FakePortal):
        def start_task_soon(self, *args: object, **kwargs: object) -> None:
            resolved.append(cast("Callable[[str | None], Awaitable[ChannelCore]]", args[2]))

    http = _FakeHttp([_FakeResp(500, text_body="boom")])
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _CapturePortal())
    _patch_gateway_http(monkeypatch, http)

    shared = str(tmp_path / "shared.sock")
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            partial(
                run_feishu,
                session_socket=shared,
                app_id="a",
                app_secret="s",
                gateway_url="http://gw",
                ai_id="ai-1",
            )
        )
        await anyio.sleep(0.1)
        cb = captured_handler["cb"]
        await cb(SimpleNamespace(sender_id="ou_1"))
        resolve_core = resolved[0]
        c1 = await resolve_core("ou_1")
        tg.cancel_scope.cancel()

    assert c1.session_socket == shared
