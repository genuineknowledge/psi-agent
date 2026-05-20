# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Workspace compatibility integration tests."""

import json
import textwrap
from pathlib import Path

import pytest

from tests.integration.conftest import MockAIServer


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


@pytest.mark.anyio
async def test_missing_tools_dir_graceful(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Agent should work with no tools/ directory."""
    mock_ai_server.set_responses([_chunk(content="no tools", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    # Workspace without tools/
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text("async def system_prompt_builder() -> str:\n    return 'test'\n")

    from psi_agent.session.tools import load_tools_from_workspace

    tools = await load_tools_from_workspace(ws / "tools")
    assert len(tools) == 0

    from psi_agent.session.agent import SessionAgent

    agent = SessionAgent(ai_socket=base_url, tools=tools, model="test", system_prompt="test")
    chunks = []
    async for c in agent.run({"role": "user", "content": "hi"}):
        chunks.append(c)
    assert len(chunks) > 0


@pytest.mark.anyio
async def test_missing_schedules_dir_graceful(tmp_path: Path) -> None:
    """No schedules/ directory should result in empty schedule list."""
    from psi_agent.session.scheduler import load_schedules_from_workspace

    schedules = await load_schedules_from_workspace(tmp_path / "nonexistent")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_missing_system_py(mock_ai_server: MockAIServer) -> None:
    """Missing systems/system.py should result in system_prompt=None."""
    mock_ai_server.set_responses([_chunk(content="no system", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    from psi_agent.session.agent import SessionAgent

    agent = SessionAgent(ai_socket=base_url, tools={}, model="test")
    assert len(agent.history) == 0  # No system prompt injected

    chunks = []
    async for c in agent.run({"role": "user", "content": "hi"}):
        chunks.append(c)
    assert len(chunks) > 0


@pytest.mark.anyio
async def test_system_prompt_builder_raises_exception_caught(tmp_path: Path) -> None:
    """If system_prompt_builder() raises, it should be caught gracefully."""
    import signal
    import subprocess
    import time

    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "echo.py").write_text("async def echo(message: str) -> str:\n    return 'ECHO'\n")
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    raise RuntimeError('bad')\n"
    )

    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"

    # Mock AI
    import socket as _sock

    from aiohttp import web

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content='ok', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    r = web.AppRunner(app)
    await r.setup()
    s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    site = web.SockSite(r, s)
    await site.start()

    try:
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "psi-agent",
                "session",
                "--workspace",
                str(ws),
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

        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if ai_socket.exists() and channel_socket.exists():
                    time.sleep(0.5)
                    break
                time.sleep(0.1)

            from aiohttp import ClientSession, ClientTimeout, UnixConnector

            timeout = ClientTimeout(total=5)
            connector = UnixConnector(path=str(channel_socket))
            async with (
                ClientSession(connector=connector, timeout=timeout) as session,
                session.post(
                    "http://localhost/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                ) as resp,
            ):
                assert resp.status == 200, f"Session should start even with bad builder: {resp.status}"
        finally:
            for p in [proc, ai_proc]:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
    finally:
        await r.cleanup()


@pytest.mark.anyio
async def test_full_workspace_normal_conversation(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Full workspace with tool should run a normal conversation."""
    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "echo.py").write_text(
        textwrap.dedent("""\
        async def echo(message: str) -> str:
            \"\"\"Echo back.

            Args:
                message: The message.
            \"\"\"
            return f"ECHO: {message}"
    """)
    )
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'You are a test assistant.'\n"
    )

    mock_ai_server.set_responses(
        [
            _chunk(content="I understand.", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    from psi_agent.session.agent import SessionAgent
    from psi_agent.session.tools import load_tools_from_workspace

    tools = await load_tools_from_workspace(ws / "tools")
    assert len(tools) == 1

    agent = SessionAgent(ai_socket=base_url, tools=tools, model="test", system_prompt="You are a test assistant.")
    chunks = []
    async for c in agent.run({"role": "user", "content": "hello"}):
        chunks.append(c)
    assert len(chunks) > 0
    content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert len(content) > 0
