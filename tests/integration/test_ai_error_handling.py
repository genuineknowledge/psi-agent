from __future__ import annotations

import contextlib
import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from tests.integration.conftest import MockAIServer, read_sse


def _chunk(content: str = "", finish_reason: str | None = None) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


async def _wait_socket(sock_path: str, timeout_sec: float = 10.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    ap = anyio.Path(sock_path)
    while anyio.current_time() < deadline:
        if await ap.exists():
            await anyio.sleep(0.3)
            return True
        await anyio.sleep(0.1)
    return False


async def _stop_process(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except Exception:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


@pytest.mark.anyio
async def test_simple_streaming_response(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses([_chunk(content="streaming works", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    socket_path = str(tmp_path / "ai.sock")
    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) > 0
        content = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)
        assert len(content) > 0
    finally:
        await _stop_process(ai_proc)


@pytest.mark.anyio
async def test_non_json_body_returns_400(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses([_chunk(content="ok", finish_reason="stop")])
    await mock_ai_server.start()

    socket_path = str(tmp_path / "ai.sock")
    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            mock_ai_server.base_url,
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post("http://localhost/v1/chat/completions", data="not json") as resp,
        ):
            body = await resp.text()
            assert resp.status >= 400 or "error" in body.lower()
    finally:
        await _stop_process(ai_proc)


@pytest.mark.anyio
async def test_upstream_connection_refused(tmp_path: Path) -> None:
    socket_path = str(tmp_path / "ai.sock")
    proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            "http://127.0.0.1:19999/v1",
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "hello")
        all_text = "".join(json.dumps(c) for c in chunks)
        assert "error" in all_text.lower()
    finally:
        await _stop_process(proc)


@pytest.mark.anyio
async def test_upstream_sse_disconnects_mid_stream(tmp_path: Path) -> None:
    """Truncated upstream SSE should be forwarded gracefully — partial content received, no crash."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content='partial')}\n\n".encode())
        return resp  # no [DONE], connection closes here

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai.sock")
    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}/v1",
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) >= 1
        content = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)
        assert "partial" in content, f"Expected partial content, got: {content[:200]}"
    finally:
        await _stop_process(ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_upstream_401_tunnelled(tmp_path: Path) -> None:

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": {"message": "Unauthorized", "type": "auth", "code": "401"}}, status=401)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai.sock")
    proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}/v1",
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) >= 1, "Expected at least 1 error chunk"
        content = chunks[0].get("choices", [{}])[0].get("delta", {}).get("content", "")
        finish = chunks[0].get("choices", [{}])[0].get("finish_reason", "")
        assert "Upstream Error 401" in content, f"Got: {content[:200]}"
        assert finish == "error", f"Expected finish_reason='error', got {finish!r}"
    finally:
        await _stop_process(proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_anthropic_empty_content_blocks(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses([_chunk(content="stop-only", finish_reason="stop")])
    await mock_ai_server.start()

    socket_path = str(tmp_path / "ai_anthro.sock")
    proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "anthropic-messages",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            mock_ai_server.base_url,
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) >= 1
    finally:
        await _stop_process(proc)


@pytest.mark.anyio
async def test_anthropic_multi_tool_use_blocks(tmp_path: Path) -> None:

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"tool_use","id":"t1","name":"bash","input":{}}}\n\n'
        )
        await resp.write(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":"{\\"cmd\\":\\"ls\\"}"}}\n\n'
        )
        await resp.write(b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n')
        await resp.write(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":1,'
            b'"content_block":{"type":"tool_use","id":"t2","name":"read_file","input":{}}}\n\n'
        )
        await resp.write(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,'
            b'"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"/tmp\\"}"}}\n\n'
        )
        await resp.write(b'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n')
        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/messages", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai_anthro.sock")
    proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "anthropic-messages",
            "--session-socket",
            socket_path,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}/v1",
        ]
    )

    try:
        assert await _wait_socket(socket_path)
        chunks = await read_sse(socket_path, "run tools")
        assert len(chunks) > 0
    finally:
        await _stop_process(proc)
        await runner.cleanup()
