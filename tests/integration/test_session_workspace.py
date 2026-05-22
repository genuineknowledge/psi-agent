from __future__ import annotations

import json
import socket
import textwrap
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.scheduler import load_schedules_from_workspace
from psi_agent.session.tools import load_tools_from_workspace
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
async def test_missing_tools_dir_graceful(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses([_chunk(content="no tools", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text("async def system_prompt_builder() -> str:\n    return 'test'\n")

    tools = await load_tools_from_workspace(ws / "tools")
    assert len(tools) == 0

    agent = SessionAgent(ai_socket=base_url, tools=tools, model="test", system_prompt="test")
    chunks = []
    async for c in agent.run({"role": "user", "content": "hi"}):
        chunks.append(c)
    assert len(chunks) > 0


@pytest.mark.anyio
async def test_missing_schedules_dir_graceful(tmp_path: Path) -> None:

    schedules = await load_schedules_from_workspace(tmp_path / "nonexistent")
    assert len(schedules) == 0


@pytest.mark.anyio
async def test_missing_system_py(mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses([_chunk(content="no system", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    agent = SessionAgent(ai_socket=base_url, tools={}, model="test")
    assert len(agent.history) == 0

    chunks = []
    async for c in agent.run({"role": "user", "content": "hi"}):
        chunks.append(c)
    assert len(chunks) > 0


@pytest.mark.anyio
async def test_system_prompt_builder_raises_exception_caught(tmp_path: Path) -> None:

    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "echo.py").write_text("async def echo(message: str) -> str:\n    return 'ECHO'\n")
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    raise RuntimeError('bad')\n"
    )

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content='ok', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            str(ws),
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ]
    )
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

    try:
        assert await _wait_socket(ai_socket)
        assert await _wait_socket(channel_socket)

        timeout = ClientTimeout(total=5)
        connector = UnixConnector(path=channel_socket)
        async with (
            ClientSession(connector=connector, timeout=timeout) as session,
            session.post(
                "http://localhost/v1/chat/completions",
                json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            assert resp.status == 200, f"Session should start even with bad builder: {resp.status}"
    finally:
        await _stop_process(proc)
        await _stop_process(ai_proc)
        await runner.cleanup()


@pytest.mark.anyio
async def test_full_workspace_normal_conversation(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
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

    tools = await load_tools_from_workspace(ws / "tools")
    assert len(tools) == 1

    agent = SessionAgent(ai_socket=base_url, tools=tools, model="test", system_prompt="You are a test assistant.")
    chunks = []
    async for c in agent.run({"role": "user", "content": "hello"}):
        chunks.append(c)
    assert len(chunks) > 0
    content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert len(content) > 0


@pytest.mark.anyio
async def test_unicode_message_handling(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Session should handle unicode/emoji messages correctly."""
    mock_ai_server.set_responses([_chunk(content="received unicode", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "echo.py").write_text(
        'async def echo(message: str) -> str:\n    return f"ECHO: {message}"\n'
    )
    (ws / "systems").mkdir()
    (ws / "systems" / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'You are a test assistant.'\n"
    )

    tools = await load_tools_from_workspace(ws / "tools")
    agent = SessionAgent(ai_socket=base_url, tools=tools, model="test", system_prompt="You are a test assistant.")

    msg = "你好世界 🌍 — emoji and unicode test"
    chunks = [c async for c in agent.run({"role": "user", "content": msg})]
    content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert len(content) > 0, "Should receive a response for unicode message"
