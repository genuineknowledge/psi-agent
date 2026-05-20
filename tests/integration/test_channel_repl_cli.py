# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Channel REPL/CLI integration tests."""

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest
from aiohttp import web

from tests.integration.conftest import MockAIServer


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


@pytest.mark.anyio
async def test_cli_sends_message_and_displays_response(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """CLI channel sends message and stdout contains the response."""
    mock_ai_server.set_responses([_chunk(content="Hello from session", finish_reason="stop")])
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
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        result = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "hello"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "Hello from session" in result.stdout, f"stdout: {result.stdout}, stderr: {result.stderr}"
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.mark.anyio
async def test_sse_reasoning_and_content_interleaved(tmp_path: Path) -> None:
    """SSE stream with reasoning_content and content should display separately."""
    import socket as _sock

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
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        result = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "test"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout + result.stderr
        assert "thinking" in out.lower()
        assert "answer" in out.lower()
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        await runner.cleanup()


@pytest.mark.anyio
async def test_multiple_choices_iterated(tmp_path: Path) -> None:
    """When SSE has multiple choices, all should be processed."""
    import socket as _sock

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
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        result = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "test"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout + result.stderr
        assert "choice0" in out
        assert "choice1" in out
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        await runner.cleanup()
