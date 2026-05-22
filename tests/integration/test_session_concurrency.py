from __future__ import annotations

import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from tests.integration.conftest import MockAIServer


async def _send_async(socket_path: str, message: str) -> int:
    body = {"model": "test", "messages": [{"role": "user", "content": message}], "stream": True}
    connector = UnixConnector(path=socket_path)
    timeout = ClientTimeout(total=5)
    async with (
        ClientSession(connector=connector, timeout=timeout) as session,
        session.post("http://localhost/v1/chat/completions", json=body) as resp,
    ):
        return resp.status


def _chunk(content: str = "", finish_reason: str | None = None) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


async def _wait_socket(sock_path: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    ap = anyio.Path(sock_path)
    while anyio.current_time() < deadline:
        if await ap.exists():
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
async def test_second_request_gets_503_when_busy(tmp_path: Path) -> None:

    async def slow_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await anyio.sleep(2.0)
        await resp.write(f"data: {_chunk(content='slow', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", slow_handler)
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
            "openai-completions",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}/v1",
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
        assert await _wait_socket(ai_socket)
        assert await _wait_socket(channel_socket)

        first_status = 0
        second_status = 0

        async def send_first() -> None:
            nonlocal first_status
            first_status = await _send_async(channel_socket, "first")

        async def send_second() -> None:
            await anyio.sleep(0.3)
            nonlocal second_status
            second_status = await _send_async(channel_socket, "second")

        async with anyio.create_task_group() as tg:
            tg.start_soon(send_first)
            tg.start_soon(send_second)

        assert first_status == 200, f"Expected 200, got {first_status}"
        assert second_status == 503, f"Expected 503, got {second_status}"

    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_third_request_succeeds_after_lock_released(tmp_path: Path) -> None:
    mock_srv = MockAIServer(tmp_path)
    mock_srv.set_responses([_chunk(content="ok", finish_reason="stop")])
    base_url = await mock_srv.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
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
        assert await _wait_socket(ai_socket)
        assert await _wait_socket(channel_socket)

        s1 = await _send_async(channel_socket, "first")
        await anyio.sleep(0.3)
        s2 = await _send_async(channel_socket, "second")
        assert s1 == 200
        assert s2 == 200
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await mock_srv.cleanup()


@pytest.mark.anyio
async def test_history_accumulation_across_requests(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(content="first reply", finish_reason="stop"),
            _chunk(content="second reply", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
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
        assert await _wait_socket(ai_socket)
        assert await _wait_socket(channel_socket)

        s1 = await _send_async(channel_socket, "first message")
        await anyio.sleep(0.5)
        s2 = await _send_async(channel_socket, "second message")
        assert s1 == 200
        assert s2 == 200

        assert len(mock_ai_server.request_bodies) >= 1, f"Got {len(mock_ai_server.request_bodies)} request(s)"
        if len(mock_ai_server.request_bodies) >= 2:
            assert len(mock_ai_server.request_bodies[1]["messages"]) > len(mock_ai_server.request_bodies[0]["messages"])
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await mock_ai_server.cleanup()
