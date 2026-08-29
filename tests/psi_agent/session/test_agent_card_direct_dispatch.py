"""Card-callback direct dispatch tests (``agent._try_direct_card_dispatch``).

The direct dispatch is the session-side short circuit for deterministic Feishu
card callbacks: a message composed entirely of ``<feishu_card_action>`` payloads,
each with ``dispatch.matched is True`` and a handler that resolves to a
registered tool accepting ``card_action_json``, runs the tool directly and skips
the AI turn. Every other shape must fall back to the ordinary AI turn — these
tests pin both sides.
"""

from __future__ import annotations

import json
import socket as _s
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from psi_agent.session.agent import AgentRunStatus, SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

_SILENT = "NO_REPLY"


def _payload(handler: str = "card_tool", matched: Any = True) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "operator_open_id": "ou_test",
        "dispatch": {"matched": matched, "handler": handler, "strategy": "action_handlers"},
    }


def _msg(*payloads: dict[str, Any]) -> str:
    return "".join(f"<feishu_card_action>{json.dumps(p, ensure_ascii=False)}</feishu_card_action>" for p in payloads)


def _make_agent(calls: list[str], fail: bool = False) -> SessionAgent:
    async def card_tool(card_action_json: str = "", user_key: str = "") -> str:
        calls.append(f"{user_key}|{card_action_json[:40]}")
        if fail:
            raise RuntimeError("boom")
        return json.dumps({"ok": True})

    tool = ToolFunction.from_callable(card_tool)
    registry = ToolRegistry(files={"test": FileEntry("", {"card_tool": tool}, {"card_tool": card_tool})})
    # AI socket points at a dead port on purpose: any test that reaches the AI
    # turn without a mock server fails loudly, proving the short circuit fired.
    return SessionAgent(ai_client=AiClient("http://127.0.0.1:1/v1"), tool_registry=registry)


class _MockAIServer:
    """Minimal mock AI SSE server (same shape as test_agent.MockAIServer)."""

    def __init__(self, tmp_path: Path) -> None:
        self._runner: web.AppRunner | None = None

    async def start(self, reply: str) -> str:
        async def handler(request: web.Request) -> web.StreamResponse:
            resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            chunk = json.dumps({"id": "mock", "choices": [{"delta": {"content": reply}, "finish_reason": "stop"}]})
            await resp.write(f"data: {chunk}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp

        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        site = web.SockSite(self._runner, sock)
        await site.start()
        return f"http://127.0.0.1:{sock.getsockname()[1]}"

    async def cleanup(self) -> None:
        if self._runner:
            await self._runner.cleanup()


@pytest.mark.anyio
async def test_matched_handler_directly_dispatches_without_ai() -> None:
    calls: list[str] = []
    agent = _make_agent(calls)
    run = agent.run_streamed({"role": "user", "content": _msg(_payload())})
    chunks = [c async for c in run]

    assert [c.content for c in chunks] == [_SILENT]
    assert len(calls) == 1 and calls[0].startswith("ou_test|")
    # 直调成功必须落终态,不能被记成 "failed or abandoned"。
    assert run.result is not None and run.result.status == AgentRunStatus.COMPLETED


@pytest.mark.anyio
async def test_matched_false_falls_back_to_ai(tmp_path: Path) -> None:
    calls: list[str] = []
    mock = _MockAIServer(tmp_path)
    ai_socket = await mock.start("model reply")
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=_make_agent(calls)._tool_registry)
        chunks = [c async for c in agent.run({"role": "user", "content": _msg(_payload(matched=False))})]
    finally:
        await mock.cleanup()

    assert "".join(c.content or "" for c in chunks) == "model reply"
    assert calls == []  # handler 绝不能被直调


@pytest.mark.anyio
async def test_missing_dispatch_falls_back_to_ai(tmp_path: Path) -> None:
    payload = _payload()
    del payload["dispatch"]
    mock = _MockAIServer(tmp_path)
    ai_socket = await mock.start("model reply")
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=_make_agent([])._tool_registry)
        chunks = [c async for c in agent.run({"role": "user", "content": _msg(payload)})]
    finally:
        await mock.cleanup()

    assert "".join(c.content or "" for c in chunks) == "model reply"


@pytest.mark.anyio
async def test_unregistered_handler_falls_back_to_ai(tmp_path: Path) -> None:
    mock = _MockAIServer(tmp_path)
    ai_socket = await mock.start("model reply")
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=_make_agent([])._tool_registry)
        chunks = [c async for c in agent.run({"role": "user", "content": _msg(_payload(handler="nope"))})]
    finally:
        await mock.cleanup()

    assert "".join(c.content or "" for c in chunks) == "model reply"


@pytest.mark.anyio
async def test_residue_text_falls_back_to_ai(tmp_path: Path) -> None:
    mock = _MockAIServer(tmp_path)
    ai_socket = await mock.start("model reply")
    try:
        agent = SessionAgent(ai_client=AiClient(ai_socket), tool_registry=_make_agent([])._tool_registry)
        content = _msg(_payload()) + "please also do X"
        chunks = [c async for c in agent.run({"role": "user", "content": content})]
    finally:
        await mock.cleanup()

    assert "".join(c.content or "" for c in chunks) == "model reply"


@pytest.mark.anyio
async def test_batch_runs_every_action() -> None:
    calls: list[str] = []
    agent = _make_agent(calls)
    content = _msg(_payload(), _payload()) + ""
    # batch 外壳被剥离后两条各执行一次
    content = "<feishu_card_action_batch>" + content + "</feishu_card_action_batch>"
    chunks = [c async for c in agent.run({"role": "user", "content": content})]

    assert [c.content for c in chunks] == [_SILENT]
    assert len(calls) == 2


@pytest.mark.anyio
async def test_tool_exception_yields_visible_error_chunk() -> None:
    agent = _make_agent([], fail=True)
    chunks = [c async for c in agent.run({"role": "user", "content": _msg(_payload())})]

    assert len(chunks) == 1
    assert chunks[0].content != _SILENT
    assert "失败" in (chunks[0].content or "")
    assert "boom" not in (chunks[0].content or "")  # 异常细节只进日志,不出对话
