from __future__ import annotations

import base64
import json
import os
import socket
import tempfile
import textwrap

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, FormData, web

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._manager import (
    AiCreateRequest,
    SessionCreateRequest,
)
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway.server import create_app
from tests.integration.conftest import MockAIServer


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
        d["reasoning"] = reasoning
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


async def _start_app_on_free_port(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return f"http://127.0.0.1:{port}", runner


def _make_workspace(base: str) -> str:
    ws = os.path.join(base, "workspace")
    tools_dir = os.path.join(ws, "tools")
    os.makedirs(tools_dir)
    with open(os.path.join(tools_dir, "echo.py"), "w") as f:
        f.write(
            textwrap.dedent("""\
            async def echo(message: str) -> str:
                \"\"\"Echo back the message.

                Args:
                    message: The message to echo.
                \"\"\"
                return f"ECHO: {message}"
        """)
        )
    systems_dir = os.path.join(ws, "systems")
    os.makedirs(systems_dir)
    with open(os.path.join(systems_dir, "system.py"), "w") as f:
        f.write(
            textwrap.dedent("""\
            async def system_prompt_builder() -> str:
                return "You are a helpful test assistant."
        """)
        )
    return ws


@pytest.mark.anyio
async def test_gateway_rest_crud(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = create_app(aim, sm)
    base_url, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{base_url}/ais",
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "api_key": "sk-test",
                    "base_url": "https://api.example.com",
                },
            ) as resp:
                assert resp.status == 201
                data = await resp.json()
                assert data["provider"] == "openai"
                ai_id = data["id"]

            async with session.get(f"{base_url}/ais") as resp:
                assert resp.status == 200
                items = await resp.json()
                assert len(items) == 1

            workspace = _make_workspace(str(tmp_path))
            async with session.post(
                f"{base_url}/sessions",
                json={
                    "ai_id": ai_id,
                    "workspace": workspace,
                },
            ) as resp:
                assert resp.status == 201
                data = await resp.json()
                assert data["ai_id"] == ai_id
                session_id = data["id"]

            async with session.get(f"{base_url}/sessions") as resp:
                assert resp.status == 200
                items = await resp.json()
                assert len(items) == 1

            async with session.delete(f"{base_url}/sessions/{session_id}") as resp:
                assert resp.status == 200

            async with session.delete(f"{base_url}/ais/{ai_id}") as resp:
                assert resp.status == 200

    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_rest_errors(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = create_app(aim, sm)
    base_url, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.delete(f"{base_url}/ais/nonexistent") as resp:
                assert resp.status == 404

            async with session.delete(f"{base_url}/sessions/nonexistent") as resp:
                assert resp.status == 404

            async with session.post(f"{base_url}/ais", json={}) as resp:
                assert resp.status == 400

    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_chat_sse(tmp_path: str, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(content="Hello from Gateway!", finish_reason="stop"),
        ]
    )
    mock_base_url = await mock_ai_server.start()

    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)

    ai_req = AiCreateRequest(
        provider="openai",
        model="test",
        api_key="k",
        base_url=mock_base_url,
        id="gw-ai",
    )
    await aim.create(ai_req)

    workspace = _make_workspace(str(tmp_path))
    sess_req = SessionCreateRequest(ai_id="gw-ai", workspace=workspace, id="gw-sess")
    await sm.create(sess_req)

    app = create_app(aim, sm)
    base_url, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        async with (
            ClientSession(timeout=timeout) as session,
            session.post(
                f"{base_url}/sessions/gw-sess/chat",
                json={"chunks": [{"type": "text", "text": "hello"}]},
            ) as resp,
        ):
            assert resp.status == 200
            chunks: list[dict] = []
            async for raw in resp.content:
                line = raw.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                chunks.append(json.loads(data_str))

        assert len(chunks) >= 1
        text_chunks = [c for c in chunks if c["type"] == "text"]
        combined = "".join(c["text"] for c in text_chunks)
        assert "Hello from Gateway!" in combined
    finally:
        await runner.cleanup()
        await sm.delete("gw-sess")
        await aim.delete("gw-ai")
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_blob_send(tmp_path: str, mock_ai_server: MockAIServer) -> None:
    data_dir = tempfile.mkdtemp(dir="/tmp", prefix="gwb")
    test_file = data_dir + "/test-out.txt"
    await anyio.Path(test_file).write_text("blob response content")

    resp_text = f"Here you go: [SEND:{test_file}]"
    mock_ai_server.set_responses(
        [
            _chunk(content=resp_text, finish_reason="stop"),
        ]
    )
    mock_base_url = await mock_ai_server.start()

    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)

    ai_req = AiCreateRequest(
        provider="openai",
        model="test",
        api_key="k",
        base_url=mock_base_url,
        id="gw-ai",
    )
    await aim.create(ai_req)

    workspace = _make_workspace(str(tmp_path))
    sess_req = SessionCreateRequest(ai_id="gw-ai", workspace=workspace, id="gw-sess")
    await sm.create(sess_req)

    app = create_app(aim, sm)
    base_url, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        form = FormData()
        blob_data = base64.b64encode(b"blob input").decode()
        form.add_field(
            "chunks",
            json.dumps(
                [
                    {"type": "text", "text": "hello"},
                    {"type": "blob", "name": "test.txt", "data": blob_data},
                ]
            ),
        )
        form.add_field("file", b"file-as-multipart", filename="upload.txt")

        async with (
            ClientSession(timeout=timeout) as session,
            session.post(
                f"{base_url}/sessions/gw-sess/chat",
                data=form,
            ) as resp,
        ):
            assert resp.status == 200
            chunks: list[dict] = []
            async for raw in resp.content:
                line = raw.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                chunks.append(json.loads(data_str))

        blob_chunks = [c for c in chunks if c["type"] == "blob"]
        assert len(blob_chunks) >= 1
        blob = blob_chunks[0]
        decoded = base64.b64decode(blob["data"])
        assert b"blob response content" in decoded
    finally:
        await runner.cleanup()
        await sm.delete("gw-sess")
        await aim.delete("gw-ai")
        await tg.__aexit__(None, None, None)
