# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""AI layer error handling integration tests."""

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

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


def _start_ai_server(tmp_path: Path, mock: MockAIServer, socket_name: str = "ai.sock") -> tuple[str, str]:
    """Start AI server subprocess and return (socket_path, base_url)."""
    base_url = mock.base_url
    socket_path = tmp_path / socket_name
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            str(socket_path),
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if socket_path.exists():
            time.sleep(0.2)
            break
        time.sleep(0.1)
    return str(socket_path), base_url, proc


@pytest.mark.anyio
async def test_simple_streaming_response(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Basic streaming: start AI server, send request, verify SSE chunks arrive."""
    await mock_ai_server.start()
    socket_path, _, proc = _start_ai_server(tmp_path, mock_ai_server)
    try:
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) > 0
        content = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "") for c in chunks)
        assert len(content) > 0
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.anyio
async def test_non_json_body_returns_400(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Non-JSON body should return 400 error."""
    await mock_ai_server.start()
    socket_path, _, proc = _start_ai_server(tmp_path, mock_ai_server)
    try:
        from aiohttp import ClientSession, ClientTimeout, UnixConnector

        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with ClientSession(connector=connector, timeout=timeout) as s:
            async with s.post("http://localhost/v1/chat/completions", data="not json") as resp:
                body = await resp.text()
                assert resp.status >= 400 or "error" in body.lower()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.anyio
async def test_upstream_connection_refused(tmp_path: Path) -> None:
    """When upstream is unreachable, AI server should return 502 error via SSE."""
    socket_path = str(tmp_path / "ai.sock")
    proc = subprocess.Popen(
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for socket
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if Path(socket_path).exists():
                time.sleep(0.2)
                break
            time.sleep(0.1)
        chunks = await read_sse(socket_path, "hello")
        all_text = "".join(json.dumps(c) for c in chunks)
        assert "error" in all_text.lower()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.anyio
async def test_upstream_sse_disconnects_mid_stream(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """When upstream SSE stream ends without [DONE], server should not hang."""
    mock_ai_server.set_responses([_chunk(content="partial")])
    await mock_ai_server.start()
    socket_path, _, proc = _start_ai_server(tmp_path, mock_ai_server)
    try:
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) >= 1
        assert any("partial" in json.dumps(c) for c in chunks)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.anyio
async def test_upstream_401_tunnelled(tmp_path: Path) -> None:
    """When upstream returns 401, error should be passed through."""
    import socket as _sock

    from aiohttp import web

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": {"message": "Unauthorized", "type": "auth", "code": "401"}}, status=401)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai.sock")
    proc = subprocess.Popen(
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if Path(socket_path).exists():
                time.sleep(0.2)
                break
            time.sleep(0.1)
        chunks = await read_sse(socket_path, "hello")
        all_text = "".join(json.dumps(c) for c in chunks)
        assert "error" in all_text.lower()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        await runner.cleanup()


@pytest.mark.anyio
async def test_anthropic_empty_content_blocks(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Anthropic layer should handle empty content blocks gracefully."""
    mock_ai_server.set_responses([_chunk(content="stop-only", finish_reason="stop")])
    await mock_ai_server.start()
    # Start Anthropic AI server
    socket_path = str(tmp_path / "ai_anthro.sock")
    proc = subprocess.Popen(
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if Path(socket_path).exists():
                time.sleep(0.2)
                break
            time.sleep(0.1)
        chunks = await read_sse(socket_path, "hello")
        assert len(chunks) >= 1
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.anyio
async def test_anthropic_multi_tool_use_blocks(tmp_path: Path) -> None:
    """Anthropic layer should correctly handle multiple tool_use blocks."""
    import socket as _sock

    from aiohttp import web

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"bash","input":{}}}\n\n'
        )
        await resp.write(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\\\"cmd\\\\":\\\\"ls\\\\"}"}}\n\n'
        )
        await resp.write(
            b'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t2","name":"read_file","input":{}}}\n\n'
        )
        await resp.write(
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\\\"path\\\\":\\\\"/tmp\\\\"}"}}\n\n'
        )
        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/messages", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai_anthro.sock")
    proc = subprocess.Popen(
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
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if Path(socket_path).exists():
                time.sleep(0.2)
                break
            time.sleep(0.1)
        chunks = await read_sse(socket_path, "run tools")
        all_text = "".join(json.dumps(c) for c in chunks)
        assert "bash" in all_text
        assert "read_file" in all_text
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        await runner.cleanup()
