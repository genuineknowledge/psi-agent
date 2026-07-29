from __future__ import annotations

import socket
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.system_prompt import SystemPrompt

_STOP_SSE = (
    b'data: {"id":"test","choices":[{"index":0,"delta":{"content":"Hello!"},'
    b'"finish_reason":"stop"}],"created":0,"model":"test",'
    b'"object":"chat.completion.chunk"}\n\n'
)
_COMPACTION_SSE = (
    b'data: {"id":"compaction","choices":[{"index":0,"delta":{},'
    b'"finish_reason":"compaction_needed"}],'
    b'"psi_compaction":{"needed":true,"prompt_tokens":50000,"threshold":10000}}\n\n'
)
_STOP_HI_SSE = (
    b'data: {"id":"test","choices":[{"index":0,"delta":{"content":"Hi"},'
    b'"finish_reason":"stop"}],"created":0,"model":"test",'
    b'"object":"chat.completion.chunk"}\n\n'
)


@pytest.mark.anyio
async def test_agent_triggers_compaction_on_signal() -> None:
    recorded_messages: list[list[dict[str, Any]]] = []

    async def compact_history_mock(history, complete_fn):
        recorded_messages.append(list(history))
        return "Mocked summary of the conversation."

    sp = SystemPrompt(
        builder=lambda: "You are a helpful assistant.",
        compaction_fn=compact_history_mock,
    )

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        await resp.write(_STOP_SSE)
        await resp.write(_COMPACTION_SSE)
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    site = web.SockSite(runner, s)
    await site.start()
    await anyio.sleep(0.05)

    try:
        conv = Conversation(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "old chat 1"},
                {"role": "assistant", "content": "old reply 1"},
            ]
        )
        ai_client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        agent = SessionAgent(
            ai_client=ai_client,
            conversation=conv,
            system_prompt=sp,
        )

        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert len(chunks) > 0
        all_content = "".join(c.content or "" for c in chunks)
        assert "Hello!" in all_content

        await anyio.sleep(0.02)

        assert len(conv.messages) >= 4
        assert conv.messages[0]["role"] == "system"
        assert conv.messages[0]["content"] == "You are helpful."
        compacted_msg = conv.messages[-1]
        assert compacted_msg["role"] == "compacted"
        assert compacted_msg["kind"] == "compacted"
        assert "Mocked summary" in compacted_msg["content"]
        assert len(recorded_messages) == 1
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_no_compaction_without_signal() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        await resp.write(_STOP_SSE)
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    site = web.SockSite(runner, s)
    await site.start()
    await anyio.sleep(0.05)

    try:
        conv = Conversation(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "old chat 1"},
                {"role": "assistant", "content": "old reply 1"},
            ]
        )
        ai_client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        agent = SessionAgent(
            ai_client=ai_client,
            conversation=conv,
            system_prompt=SystemPrompt(builder=lambda: "You are helpful."),
        )

        chunks = [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert len(chunks) > 0
        all_content = "".join(c.content or "" for c in chunks)
        assert "Hello!" in all_content

        assert len(conv.messages) >= 3
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_compaction_creates_system_if_missing() -> None:
    async def compact_history_mock(history, complete_fn):
        return "Compacted summary."

    sp = SystemPrompt(compaction_fn=compact_history_mock)

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        await resp.write(_STOP_HI_SSE)
        await resp.write(_COMPACTION_SSE)
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    site = web.SockSite(runner, s)
    await site.start()
    await anyio.sleep(0.05)

    try:
        conv = Conversation(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        )
        ai_client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        agent = SessionAgent(
            ai_client=ai_client,
            conversation=conv,
            system_prompt=sp,
        )

        [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert len(conv.messages) >= 3
        compacted_msg = conv.messages[-1]
        assert compacted_msg["role"] == "compacted"
        assert compacted_msg["kind"] == "compacted"
        assert "Compacted summary." in compacted_msg["content"]
    finally:
        await runner.cleanup()
