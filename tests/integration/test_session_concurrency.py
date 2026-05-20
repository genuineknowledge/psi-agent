# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Session concurrency and lock integration tests."""

import json
import signal
import subprocess
import time
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from tests.integration.conftest import MockAIServer


async def _send_async(socket_path: str, message: str) -> int:
    body = {"model": "test", "messages": [{"role": "user", "content": message}], "stream": True}
    connector = UnixConnector(path=socket_path)
    timeout = ClientTimeout(total=5)
    async with ClientSession(connector=connector, timeout=timeout) as session:
        async with session.post("http://localhost/v1/chat/completions", json=body) as resp:
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


def _wait_for_socket(sock_path: Path, timeout_sec: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if sock_path.exists():
            time.sleep(0.3)
            return True
        time.sleep(0.1)
    return False


def _killall(*procs: subprocess.Popen) -> None:
    for p in procs:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


@pytest.mark.anyio
async def test_second_request_gets_503_when_busy(tmp_path: Path) -> None:
    """When session is processing a request, a second concurrent request gets 503."""
    import socket as _sock

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
    sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"

    ai_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            str(ai_socket),
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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert _wait_for_socket(ai_socket)
        assert _wait_for_socket(channel_socket)

        first_status = 0
        second_status = 0

        async def send_first() -> None:
            nonlocal first_status
            first_status = await _send_async(str(channel_socket), "first")

        async def send_second() -> None:
            await anyio.sleep(0.3)
            nonlocal second_status
            second_status = await _send_async(str(channel_socket), "second")

        async with anyio.create_task_group() as tg:
            tg.start_soon(send_first)
            tg.start_soon(send_second)

        assert first_status == 200, f"Expected 200, got {first_status}"
        assert second_status == 503, f"Expected 503, got {second_status}"

    finally:
        _killall(ses_proc, ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_third_request_succeeds_after_lock_released(tmp_path: Path) -> None:
    """After first request completes, a subsequent request succeeds."""
    mock_srv = MockAIServer(tmp_path)
    mock_srv.set_responses([_chunk(content="ok", finish_reason="stop")])
    base_url = await mock_srv.start()

    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"

    ai_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            str(ai_socket),
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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert _wait_for_socket(ai_socket)
        assert _wait_for_socket(channel_socket)

        s1 = await _send_async(str(channel_socket), "first")
        await anyio.sleep(0.3)
        s2 = await _send_async(str(channel_socket), "second")
        assert s1 == 200
        assert s2 == 200
    finally:
        _killall(ses_proc, ai_proc)
        await mock_srv.cleanup()


@pytest.mark.anyio
async def test_history_accumulation_across_requests(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Session should accumulate history across multiple channel requests."""
    mock_ai_server.set_responses(
        [
            _chunk(content="first reply", finish_reason="stop"),
            _chunk(content="second reply", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"

    ai_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            str(ai_socket),
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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert _wait_for_socket(ai_socket)
        assert _wait_for_socket(channel_socket)

        s1 = await _send_async(str(channel_socket), "first message")
        await anyio.sleep(0.5)
        s2 = await _send_async(str(channel_socket), "second message")
        assert s1 == 200
        assert s2 == 200

        assert len(mock_ai_server.request_bodies) == 2
        assert len(mock_ai_server.request_bodies[1]["messages"]) > len(mock_ai_server.request_bodies[0]["messages"])
    finally:
        _killall(ses_proc, ai_proc)
        await mock_ai_server.cleanup()
