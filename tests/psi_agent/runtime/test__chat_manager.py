"""``ChatManager.handle`` 往 ``/chat/completions`` 的消费方 (desktop) 转发什么。

判据落在 ``handle`` 本身而不是更下层的 ``ChannelCore``: 丢字段的那一跳就在
``handle`` 里 —— 下层早就把 ``tool_name`` / ``tool_args`` 带到了它手上, 是它只往外
抄了 ``kind``。拿 ``ChannelCore`` 单独测只能证明"下层没丢", 而下层本来就没丢。

``ChannelCore`` 用假货替掉: 真货要连 unix socket / 命名管道, 而那条路在 Windows 上
恒失败 (asyncio 子进程 ``NotImplementedError``), 判据落在那里等于没有判据。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from psi_agent.channel._types import OutputChunk, ReasoningChunk, TextChunk
from psi_agent.runtime import _chat_manager
from psi_agent.runtime._chat_manager import ChatManager


class _FakeCore:
    """替掉 ``ChannelCore``: 原样吐出预置的 chunk, 不碰网络。"""

    def __init__(self, *chunks: OutputChunk) -> None:
        self._chunks = chunks

    def __call__(self, **_kwargs: Any) -> _FakeCore:
        return self

    async def __aenter__(self) -> _FakeCore:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _chunks: list[Any]) -> AsyncGenerator[OutputChunk]:
        for c in self._chunks:
            yield c


async def _collect(monkeypatch: pytest.MonkeyPatch, *chunks: OutputChunk) -> list[dict[str, Any]]:
    monkeypatch.setattr(_chat_manager, "ChannelCore", _FakeCore(*chunks))
    body = {"chunks": [{"type": "text", "text": "hi"}]}
    return [event async for event in ChatManager().handle("/tmp/session.sock", body)]


@pytest.mark.anyio
async def test_reasoning_forward_carries_tool_name_and_args(monkeypatch: pytest.MonkeyPatch):
    """转发 reasoning 时 ``tool_name`` / ``tool_args`` 都要带上。

    只带 ``kind`` 的话消费方知道"这是一次工具调用"却不知道是哪个工具, 只能显示
    兜底文案 —— 而这两个字段一路从 session 传到了 ``handle`` 手上。
    """
    args = '{"command": "echo )]"}'
    events = await _collect(
        monkeypatch,
        ReasoningChunk(text=f"[Tool Call: bash({args})]", kind="tool_call", tool_name="bash", tool_args=args),
    )

    assert len(events) == 1, f"转发的事件数不对: {events!r}"
    assert events[0]["type"] == "reasoning"
    assert events[0]["kind"] == "tool_call"
    assert events[0]["tool_name"] == "bash", "工具名断在最后一跳"
    # 参数里字面含 ")]" —— 走字段就该逐字节原样过去, 不存在截断。
    assert events[0]["tool_args"] == args, "参数断在最后一跳或被截断"


@pytest.mark.anyio
async def test_reasoning_without_tool_fields_omits_the_keys(monkeypatch: pytest.MonkeyPatch):
    """纯思考不带工具字段时**不加 key** —— 消费方看不到 null, 不必特判。"""
    events = await _collect(monkeypatch, ReasoningChunk(text="想一下", kind="thinking"))

    assert events == [{"type": "reasoning", "text": "想一下", "kind": "thinking"}]


@pytest.mark.anyio
async def test_text_chunks_forward_unchanged(monkeypatch: pytest.MonkeyPatch):
    """正文那条路一个字都没动 —— 上面两条改的只是 reasoning 分支。"""
    events = await _collect(monkeypatch, TextChunk("结论"))

    assert events == [{"type": "text", "text": "结论"}]
