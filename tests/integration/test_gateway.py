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


async def _make_workspace(base: str) -> str:
    ws = os.path.join(base, "workspace")
    tools_dir = os.path.join(ws, "tools")
    await anyio.Path(tools_dir).mkdir(parents=True)
    await anyio.Path(tools_dir, "echo.py").write_text(
        textwrap.dedent("""\
        async def echo(message: str) -> str:
            \"\"\"Echo back the message.

            Args:
                message: The message to echo.
            \"\"\"
            return f"ECHO: {message}"
    """),
        encoding="utf-8",
    )
    systems_dir = os.path.join(ws, "systems")
    await anyio.Path(systems_dir).mkdir(parents=True)
    await anyio.Path(systems_dir, "system.py").write_text(
        textwrap.dedent("""\
        async def system_prompt_builder() -> str:
            return "You are a helpful test assistant."
    """),
        encoding="utf-8",
    )
    return ws


@pytest.mark.anyio
async def test_gateway_rest_crud(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = await create_app(aim, sm)
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

            workspace = await _make_workspace(str(tmp_path))
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
    app = await create_app(aim, sm)
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

    workspace = await _make_workspace(str(tmp_path))
    sess_req = SessionCreateRequest(ai_id="gw-ai", workspace=workspace, id="gw-sess")
    await sm.create(sess_req)

    app = await create_app(aim, sm)
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
    await anyio.Path(test_file).write_text("blob response content", encoding="utf-8")

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

    workspace = await _make_workspace(str(tmp_path))
    sess_req = SessionCreateRequest(ai_id="gw-ai", workspace=workspace, id="gw-sess")
    await sm.create(sess_req)

    app = await create_app(aim, sm)
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


@pytest.mark.anyio
async def test_gateway_favicon(tmp_path: str) -> None:
    icon_dir = tempfile.mkdtemp(dir="/tmp", prefix="gwfav")
    icon_path = icon_dir + "/icon.png"
    icon_bytes = b"\x89PNG\r\n\x1a\n-fake-favicon-bytes"
    await anyio.Path(icon_path).write_bytes(icon_bytes)

    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)

    app_with = await create_app(aim, sm, favicon_path=icon_path)
    base_with, runner_with = await _start_app_on_free_port(app_with)

    app_without = await create_app(aim, sm)
    base_without, runner_without = await _start_app_on_free_port(app_without)

    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_with}/favicon.ico") as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith("image/")
                assert await resp.read() == icon_bytes
            async with session.get(f"{base_without}/favicon.ico") as resp:
                assert resp.status == 404
    finally:
        await runner_with.cleanup()
        await runner_without.cleanup()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_session_manager_reset_keeps_session(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    try:
        ai = await aim.create(
            AiCreateRequest(
                provider="openai",
                model="gpt-4o",
                api_key="sk-test",
                base_url="https://api.example.com",
            )
        )
        workspace = await _make_workspace(str(tmp_path))
        created = await sm.create(
            SessionCreateRequest(ai_id=ai.id, workspace=workspace, id="reset-sess")
        )
        assert created.id == "reset-sess"

        info = await sm.reset("reset-sess")

        assert info.id == "reset-sess"
        assert info.ai_id == ai.id
        assert info.workspace == workspace
        sessions = await sm.list_all()
        assert any(s.id == "reset-sess" for s in sessions)
    finally:
        await sm.delete("reset-sess")
        await aim.delete(ai.id)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_reset_deletes_history(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = await create_app(aim, sm)
    base_url, runner = await _start_app_on_free_port(app)
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            ai = await aim.create(
                AiCreateRequest(
                    provider="openai",
                    model="gpt-4o",
                    api_key="sk-test",
                    base_url="https://api.example.com",
                )
            )
            workspace = await _make_workspace(str(tmp_path))
            async with session.post(
                f"{base_url}/sessions",
                json={"ai_id": ai.id, "workspace": workspace, "id": "reset-http"},
            ) as resp:
                assert resp.status == 201

            hist_dir = os.path.join(workspace, "histories")
            await anyio.Path(hist_dir).mkdir(parents=True, exist_ok=True)
            hist_file = os.path.join(hist_dir, "reset-http.jsonl")
            await anyio.Path(hist_file).write_text(
                '{"role": "user", "content": "hi"}\n', encoding="utf-8"
            )
            assert await anyio.Path(hist_file).exists()

            async with session.post(
                f"{base_url}/titles", json={"id": "reset-http", "title": "旧标题"}
            ) as resp:
                assert resp.status == 200

            async with session.post(
                f"{base_url}/sessions/reset-http/reset"
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["id"] == "reset-http"

            assert not await anyio.Path(hist_file).exists()

            async with session.get(f"{base_url}/titles") as resp:
                assert resp.status == 200
                titles = await resp.json()
                assert "reset-http" not in titles

            async with session.post(
                f"{base_url}/sessions/nonexistent/reset"
            ) as resp:
                assert resp.status == 404
    finally:
        await runner.cleanup()
        await sm.delete("reset-http")
        await aim.delete(ai.id)
        await tg.__aexit__(None, None, None)
