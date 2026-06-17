from __future__ import annotations

import json
import socket as _sock

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from tests.integration.conftest import MockAIServer, read_sse


def _chunk(content: str = "", reasoning: str = "", finish_reason: str | None = None) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    if reasoning:
        d["reasoning_content"] = reasoning
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


async def _read_tcp_sse(base_url: str, message: str = "hello") -> list[dict]:
    chunks: list[dict] = []
    body = {"model": "test", "messages": [{"role": "user", "content": message}], "stream": True}
    timeout = ClientTimeout(total=10)
    async with (
        ClientSession(timeout=timeout) as session,
        session.post(base_url.rstrip("/") + "/chat/completions", json=body) as resp,
    ):
        assert resp.status == 200, f"Got {resp.status}"
        async for raw in resp.content:
            line = raw.decode().strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunks.append(json.loads(data_str))
            except json.JSONDecodeError:
                continue
    return chunks


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
async def test_channel_receives_content_from_session(tmp_path, mock_ai_server: MockAIServer) -> None:
    """Channel should receive streaming content from session via SSE."""
    mock_ai_server.set_responses([_chunk(content="Hello from session", finish_reason="stop")])
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

        chunks = await read_sse(channel_socket, "hello")
        content = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)
        assert "Hello from session" in content, f"Got: {content[:200]}"
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)


@pytest.mark.anyio
async def test_sse_reasoning_and_content_interleaved(tmp_path) -> None:
    """SSE stream with reasoning_content and content should be parsed separately."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(reasoning='thinking...')}\n\n".encode())
        await resp.write(f"data: {_chunk(content='answer')}\n\n".encode())
        await resp.write(f"data: {_chunk(reasoning='more thinking')}\n\n".encode())
        await resp.write(f"data: {_chunk(content=' final', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    try:
        chunks = await _read_tcp_sse(f"http://127.0.0.1:{port}/v1", "test")
        reasonings = [c.get("choices", [{}])[0].get("delta", {}).get("reasoning_content", "") for c in chunks]
        contents = [c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks]
        assert any("thinking" in r for r in reasonings)
        assert any("answer" in c for c in contents)
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_multiple_choices_iterated(tmp_path) -> None:
    """When SSE has multiple choices, all deltas should be extracted."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [
                {"index": 0, "delta": {"content": "choice0"}, "finish_reason": None},
                {"index": 1, "delta": {"content": "choice1"}, "finish_reason": "stop"},
            ],
        }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    try:
        chunks = await _read_tcp_sse(f"http://127.0.0.1:{port}/v1", "test")
        all_text = json.dumps(chunks)
        assert "choice0" in all_text
        assert "choice1" in all_text
    finally:
        await runner.cleanup()
