from __future__ import annotations

import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import web

from tests.integration.conftest import MockAIServer, read_sse


def _chunk(
    content: str = "",
    reasoning: str = "",
    tool_calls: list | None = None,
    finish_reason: str | None = None,
) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    if reasoning:
        d["reasoning_content"] = reasoning
    if tool_calls:
        d["tool_calls"] = tool_calls
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


async def _wait_for_socket(sock_path: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    sock_anyio = anyio.Path(sock_path)
    while anyio.current_time() < deadline:
        if await sock_anyio.exists():
            await anyio.sleep(0.3)
            return True
        await anyio.sleep(0.1)
    return False


async def _stop_process(proc) -> None:
    proc.terminate()
    try:
        await proc.wait()
    except Exception:
        proc.kill()


@pytest.mark.anyio
async def test_full_pipeline_mock_ai(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Full pipeline: mock AI -> session -> SSE read."""
    mock_ai_server.set_responses([_chunk(content="pipeline works", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "--provider",
            "openai",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
        ],
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ],
    )

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)

        chunks = await read_sse(channel_socket, "test pipeline")
        content = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)
        assert "pipeline works" in content, f"Got: {content[:200]}"
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)


@pytest.mark.anyio
async def test_full_pipeline_with_tool(tmp_path: Path) -> None:
    """Full pipeline with tool call: mock AI -> tool execution -> final response."""

    req_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        req_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if req_count == 1:
            tc = _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"message":"tool test"}',
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
            await resp.write(f"data: {tc}\n\n".encode())
        else:
            await resp.write(f"data: {_chunk(content='Final: tool was called', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "--provider",
            "openai",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}",
        ],
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ],
    )

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)
        chunks = await read_sse(channel_socket, "use tool")
        all_text = json.dumps(chunks)
        assert "tool was called" in all_text, f"Got: {all_text[:500]}"
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_multiple_messages_history_accumulates(tmp_path: Path) -> None:
    """Two channel messages should cause history accumulation in session."""

    req_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        req_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content=f'response {req_count}', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "--provider",
            "openai",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}",
        ],
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ],
    )

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)
        chunks1 = await read_sse(channel_socket, "msg1")
        await anyio.sleep(0.3)
        chunks2 = await read_sse(channel_socket, "msg2")
        c1 = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks1)
        c2 = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks2)
        assert "response 1" in c1, f"Got: {c1}"
        assert "response 2" in c2, f"Got: {c2}"
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_multi_turn_history_accumulates(tmp_path: Path) -> None:
    """Three channel messages should accumulate history correctly."""
    req_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        req_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content=f'turn {req_count}', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "--provider",
            "openai",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}",
        ]
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ]
    )

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)
        chunks1 = await read_sse(channel_socket, "msg1")
        await anyio.sleep(0.3)
        chunks2 = await read_sse(channel_socket, "msg2")
        await anyio.sleep(0.3)
        chunks3 = await read_sse(channel_socket, "msg3")
        c1 = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks1)
        c2 = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks2)
        c3 = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks3)
        assert "turn 1" in c1
        assert "turn 2" in c2
        assert "turn 3" in c3
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await runner.cleanup()
