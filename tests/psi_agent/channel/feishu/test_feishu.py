from __future__ import annotations

import json
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
    _handle_and_stream,
    _handle_approval_event,
    _handle_comment,
    _parse_instance_detail,
    _register_approval_processor,
    _remove_reaction,
    _SeenEvents,
    run_feishu,
)


def _resolver(core: ChannelCore):
    """把固定 core 包成 resolve_core(open_id, ...) 回调 (handler 现按会话解析 core)。"""

    async def _resolve(open_id: str | None, *, chat_id: str = "", chat_type: str = "") -> ChannelCore:
        return core

    return _resolve


def _recording_resolver(core: ChannelCore):
    """同 _resolver, 但记录每次调用的路由参数, 供断言 handler 传了什么。"""
    calls: list[dict] = []

    async def _resolve(open_id: str | None, *, chat_id: str = "", chat_type: str = "") -> ChannelCore:
        calls.append({"open_id": open_id, "chat_id": chat_id, "chat_type": chat_type})
        return core

    return _resolve, calls


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

    await _handle_and_stream(channel, _resolver(core), None, ctx)

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

    await _handle_and_stream(channel, _resolver(core), None, ctx)

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

    await _handle_and_stream(channel, _resolver(core), None, ctx)

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

    await _handle_comment(channel, _resolver(core), None, _comment_event())

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

    await _handle_comment(channel, _resolver(core), None, _comment_event())

    replied_ctx = channel.reply_comment.call_args.args[0]
    assert replied_ctx.is_whole is True


@pytest.mark.anyio
async def test_handle_comment_skips_when_not_mentioned(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _resolver(core), None, _comment_event(mentioned_bot=False))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_respects_whitelist(tmp_path):
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _resolver(core), ["ou_allowed"], _comment_event(operator_open_id="ou_blocked"))

    channel.resolve_comment_target.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_skips_unsupported_target(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="x"))
    channel = _comment_channel(supported=False)
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _resolver(core), None, _comment_event())

    channel.get_comment_context.assert_not_awaited()
    channel.reply_comment.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_comment_replies_error_on_agent_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(side_effect=RuntimeError("agent boom")))
    channel = _comment_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_comment(channel, _resolver(core), None, _comment_event())

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
    await _handle_comment(channel, _resolver(core), None, _comment_event())


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


class _FakeResp:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> dict:
        return self._payload

    async def text(self) -> str:
        return self._text


class _FakeHttp:
    """记录 POST 调用次数, 按序返回预置响应。"""

    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = responses
        self.post_calls: list[dict] = []

    def post(self, url: str, json: dict, timeout: object) -> _FakeResp:
        self.post_calls.append({"url": url, "json": json})
        return self._responses.pop(0)


@pytest.mark.anyio
async def test_gateway_route_provider_caches_socket() -> None:
    http = _FakeHttp([_FakeResp(201, {"channel_socket": "/tmp/feishu-ou_1.sock"})])
    provider = client._GatewayRouteProvider("http://127.0.0.1:9000/", cast("Any", http))

    socket1 = await provider.ensure("ou_1")
    assert socket1 == "/tmp/feishu-ou_1.sock"
    # 二次命中缓存, 不再打 Gateway。
    socket2 = await provider.ensure("ou_1")
    assert socket2 == socket1
    assert len(http.post_calls) == 1
    assert http.post_calls[0]["url"] == "http://127.0.0.1:9000/feishu/route"
    assert http.post_calls[0]["json"] == {"open_id": "ou_1", "chat_id": "", "chat_type": ""}


@pytest.mark.anyio
async def test_gateway_route_provider_raises_on_failure_and_does_not_cache() -> None:
    http = _FakeHttp(
        [
            _FakeResp(500, text="boom"),
            _FakeResp(201, {"channel_socket": "/tmp/ok.sock"}),
        ]
    )
    provider = client._GatewayRouteProvider("http://127.0.0.1:9000", cast("Any", http))

    with pytest.raises(RuntimeError, match="feishu/route failed"):
        await provider.ensure("ou_1")
    # 失败不写缓存, 下条消息重试成功。
    socket = await provider.ensure("ou_1")
    assert socket == "/tmp/ok.sock"
    assert len(http.post_calls) == 2


# ── approval status-change push ───────────────────────────────────────────────


def _instance_resp(status: str = "APPROVED", applicant: str = "ou_applicant", name: str = "请假") -> SimpleNamespace:
    """Fake SDK arequest response carrying an approval instance detail body."""
    body = {"code": 0, "msg": "success", "data": {"user_id": applicant, "approval_name": name, "status": status}}
    content = json.dumps(body).encode("utf-8")
    return SimpleNamespace(code=0, msg="success", raw=SimpleNamespace(content=content))


def _approval_channel(instance_resp: SimpleNamespace | Exception | None = None) -> MagicMock:
    channel = MagicMock()
    if isinstance(instance_resp, Exception):
        channel.client.arequest = AsyncMock(side_effect=instance_resp)
    else:
        channel.client.arequest = AsyncMock(return_value=instance_resp or _instance_resp())
    channel.send = AsyncMock()
    return channel


def _approval_event(instance_code: str = "inst_1", approval_code: str = "appr_1", status: str = "APPROVED") -> Any:
    return SimpleNamespace(event={"instance_code": instance_code, "approval_code": approval_code, "status": status})


def test_parse_instance_detail_extracts_applicant():
    detail = _parse_instance_detail(_instance_resp(status="REJECTED", applicant="ou_x", name="报销"))
    assert detail == {"applicant_open_id": "ou_x", "approval_name": "报销", "status": "REJECTED"}


def test_parse_instance_detail_bad_body_returns_empty():
    assert _parse_instance_detail(SimpleNamespace(raw=SimpleNamespace(content=b"not json"))) == {}
    assert _parse_instance_detail(SimpleNamespace(raw=None)) == {}


def test_seen_events_dedup_and_bound():
    seen = _SeenEvents(maxlen=2)
    assert seen.add_if_new("a") is True
    assert seen.add_if_new("a") is False  # duplicate
    assert seen.add_if_new("b") is True
    assert seen.add_if_new("c") is True  # evicts "a"
    assert seen.add_if_new("a") is True  # "a" was evicted, seen as new again


@pytest.mark.anyio
async def test_handle_approval_event_pushes_dm_to_applicant(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="你的请假已通过"))
    channel = _approval_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_approval_event(channel, _resolver(core), None, _SeenEvents(), _approval_event())

    channel.send.assert_awaited_once()
    args = channel.send.call_args.args
    assert args[0] == "ou_applicant"
    assert args[1] == {"text": "你的请假已通过"}
    assert args[2] == {"receive_id_type": "open_id"}


@pytest.mark.anyio
async def test_handle_approval_event_respects_whitelist(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="x"))
    channel = _approval_channel(_instance_resp(applicant="ou_blocked"))
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_approval_event(channel, _resolver(core), ["ou_allowed"], _SeenEvents(), _approval_event())

    channel.send.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_approval_event_dedups_redelivery(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="ok"))
    channel = _approval_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    seen = _SeenEvents()

    await _handle_approval_event(channel, _resolver(core), None, seen, _approval_event())
    await _handle_approval_event(channel, _resolver(core), None, seen, _approval_event())

    channel.send.assert_awaited_once()  # second delivery deduped


@pytest.mark.anyio
async def test_handle_approval_event_no_applicant_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="x"))
    channel = _approval_channel(_instance_resp(applicant=""))
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    await _handle_approval_event(channel, _resolver(core), None, _SeenEvents(), _approval_event())

    channel.send.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_approval_event_swallows_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(side_effect=RuntimeError("agent boom")))
    channel = _approval_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))

    # Must not raise even though the agent call fails.
    await _handle_approval_event(channel, _resolver(core), None, _SeenEvents(), _approval_event())
    channel.send.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_approval_event_missing_instance_code_skips(tmp_path):
    channel = _approval_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    event = SimpleNamespace(event={"approval_code": "appr_1", "status": "APPROVED"})

    await _handle_approval_event(channel, _resolver(core), None, _SeenEvents(), event)

    channel.client.arequest.assert_not_awaited()
    channel.send.assert_not_awaited()


def test_register_approval_processor_injects_both_schemas():
    proc_map: dict = {}
    channel = SimpleNamespace(dispatcher=SimpleNamespace(_processorMap=proc_map))

    ok = _register_approval_processor(channel, lambda _e: None)

    assert ok is True
    assert "p1.approval_instance" in proc_map
    assert "p2.approval_instance" in proc_map


def test_register_approval_processor_degrades_without_processor_map():
    channel = SimpleNamespace(dispatcher=SimpleNamespace())  # no _processorMap
    assert _register_approval_processor(channel, lambda _e: None) is False


@pytest.mark.anyio
async def test_run_feishu_registers_approval_processor(monkeypatch):
    channel = MagicMock()
    channel.on = MagicMock()
    channel.start_background = AsyncMock()
    channel.stop_background = AsyncMock()
    channel.bot_identity = SimpleNamespace(open_id="ou_bot", name="Haitun")
    calls: list = []
    monkeypatch.setattr(client, "FeishuChannel", lambda **kw: channel)
    monkeypatch.setattr(client, "BlockingPortal", lambda: _FakePortal())
    monkeypatch.setattr(client, "_register_approval_processor", lambda ch, cb: calls.append(ch) or True)

    async with anyio.create_task_group() as tg:
        tg.start_soon(partial(run_feishu, session_socket="/tmp/x.sock", app_id="a", app_secret="s"))
        await anyio.sleep(0.1)
        tg.cancel_scope.cancel()

    assert calls == [channel]


@pytest.mark.anyio
async def test_handle_group_message_routes_by_chat_id(monkeypatch, tmp_path):
    """群消息把 chat_id/chat_type 交给 resolve_core, 以便整群共用一个 session。"""
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    channel = _fake_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    resolve, calls = _recording_resolver(core)
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_group", chat_type="group", message_id="om_1")

    await _handle_and_stream(channel, resolve, None, ctx)

    assert calls == [{"open_id": "ou_1", "chat_id": "oc_group", "chat_type": "group"}]


@pytest.mark.anyio
async def test_handle_p2p_message_passes_p2p_chat_type(monkeypatch, tmp_path):
    """私聊也如实传 chat_type=p2p, 由 Gateway 决定按 open_id 路由。"""
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    channel = _fake_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    resolve, calls = _recording_resolver(core)
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_dm", chat_type="p2p", message_id="om_1")

    await _handle_and_stream(channel, resolve, None, ctx)

    assert calls == [{"open_id": "ou_1", "chat_id": "oc_dm", "chat_type": "p2p"}]


@pytest.mark.anyio
async def test_handle_message_without_chat_type_degrades_to_empty(monkeypatch, tmp_path):
    """ctx 无 chat_type 属性 (老事件/精简 ctx) 时传空串, 不抛 AttributeError。"""
    monkeypatch.setattr(client.platformdirs, "user_downloads_dir", lambda: str(tmp_path))
    monkeypatch.setattr(client, "_build_chunks", AsyncMock(return_value=[TextChunk("hi")]))

    channel = _fake_channel()
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    resolve, calls = _recording_resolver(core)
    ctx = SimpleNamespace(sender_id="ou_1", chat_id="oc_1", message_id="om_1")

    await _handle_and_stream(channel, resolve, None, ctx)

    assert calls == [{"open_id": "ou_1", "chat_id": "oc_1", "chat_type": ""}]


@pytest.mark.anyio
async def test_comment_resolves_core_per_operator(monkeypatch, tmp_path):
    """文档评论不属于任何群聊, 故只按评论人 open_id 路由 (不传 chat_*)。"""
    core = ChannelCore(session_socket=str(tmp_path / "x.sock"))
    resolve, calls = _recording_resolver(core)
    monkeypatch.setattr(client, "_collect_reply", AsyncMock(return_value="ok"))
    channel = _comment_channel()

    await _handle_comment(channel, resolve, None, _comment_event())

    assert calls == [{"open_id": "ou_1", "chat_id": "", "chat_type": ""}]


@pytest.mark.anyio
async def test_gateway_route_provider_keys_group_by_chat_id() -> None:
    """群聊按 chat_id 缓存/请求; 同群不同发送者只打 Gateway 一次。"""
    http = _FakeHttp([_FakeResp(201, {"channel_socket": "/tmp/feishu-chat.sock"})])
    provider = client._GatewayRouteProvider("http://127.0.0.1:9000", cast("Any", http))

    s1 = await provider.ensure("ou_1", chat_id="oc_group", chat_type="group")
    s2 = await provider.ensure("ou_2", chat_id="oc_group", chat_type="group")

    assert s1 == s2 == "/tmp/feishu-chat.sock"
    assert len(http.post_calls) == 1
    assert http.post_calls[0]["json"] == {
        "open_id": "ou_1",
        "chat_id": "oc_group",
        "chat_type": "group",
    }


@pytest.mark.anyio
async def test_gateway_route_provider_p2p_and_group_do_not_share_cache() -> None:
    """同一个人的私聊与其所在群是两个不同的路由键, 各打一次 Gateway。"""
    http = _FakeHttp(
        [
            _FakeResp(201, {"channel_socket": "/tmp/dm.sock"}),
            _FakeResp(201, {"channel_socket": "/tmp/group.sock"}),
        ]
    )
    provider = client._GatewayRouteProvider("http://127.0.0.1:9000", cast("Any", http))

    dm = await provider.ensure("ou_1", chat_id="oc_dm", chat_type="p2p")
    grp = await provider.ensure("ou_1", chat_id="oc_group", chat_type="group")

    assert dm == "/tmp/dm.sock"
    assert grp == "/tmp/group.sock"
    assert len(http.post_calls) == 2


@pytest.mark.anyio
async def test_gateway_route_provider_group_cache_is_per_chat() -> None:
    """不同群各自独立缓存, 互不复用。"""
    http = _FakeHttp(
        [
            _FakeResp(201, {"channel_socket": "/tmp/a.sock"}),
            _FakeResp(201, {"channel_socket": "/tmp/b.sock"}),
        ]
    )
    provider = client._GatewayRouteProvider("http://127.0.0.1:9000", cast("Any", http))

    a = await provider.ensure("ou_1", chat_id="oc_a", chat_type="group")
    b = await provider.ensure("ou_1", chat_id="oc_b", chat_type="group")

    assert (a, b) == ("/tmp/a.sock", "/tmp/b.sock")
    assert len(http.post_calls) == 2
