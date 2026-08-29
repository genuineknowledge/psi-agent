from __future__ import annotations

import base64
import json
import os
import socket
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, FormData, web

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._attention import AttentionHub
from psi_agent.gateway._router_manager import RouterManager
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._title_manager import TitleManager
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
async def test_gateway_rest_crud(tmp_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ready(_path: str) -> None:
        await anyio.sleep(0.001)

    async def serve(**_kwargs: object) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.gateway._router_manager._wait_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._remove_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._run_router_service", serve)
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    rm = RouterManager(_aim=aim, _prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _rm=rm, _prefix="gw-test", _tg=tg)
    app = await create_app(aim, sm, TitleManager(), rm=rm)
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
                aggregator_ai_id = data["id"]

            async with session.post(
                f"{base_url}/ais",
                json={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-upstream",
                    "base_url": "https://api.example.com",
                },
            ) as resp:
                assert resp.status == 201
                upstream_ai_id = (await resp.json())["id"]

            async with session.get(f"{base_url}/ais") as resp:
                assert resp.status == 200
                items = await resp.json()
                assert len(items) == 2

            async with session.post(
                f"{base_url}/routers",
                json={
                    "name": "fallback-leaf",
                    "mode": "fallback",
                    "router_ai_id": None,
                    "upstreams": [
                        {
                            "backend_type": "ai",
                            "backend_id": upstream_ai_id,
                            "description": "general tasks",
                        }
                    ],
                    "router_timeout": None,
                    "target_timeout": 8,
                    "max_context_chars": 9_000,
                },
            ) as resp:
                assert resp.status == 201
                fallback_info = await resp.json()
                fallback_id = fallback_info["id"]
                assert fallback_info["router_ai_id"] is None
                assert fallback_info["upstreams"] == [
                    {
                        "backend_type": "ai",
                        "backend_id": upstream_ai_id,
                        "description": "general tasks",
                    }
                ]

            async with session.post(
                f"{base_url}/routers",
                json={
                    "name": "smart",
                    "mode": "aggregation",
                    "router_ai_id": aggregator_ai_id,
                    "upstreams": [
                        {
                            "backend_type": "router",
                            "backend_id": fallback_id,
                            "description": "resilient general tasks",
                        }
                    ],
                    "router_timeout": 30,
                    "target_timeout": 8,
                    "max_context_chars": 9_000,
                },
            ) as resp:
                assert resp.status == 201
                router_info = await resp.json()
                router_id = router_info["id"]
                assert router_info["router_ai_id"] == aggregator_ai_id
                assert router_info["target_timeout"] == 8
                assert router_info["max_context_chars"] == 9_000
                assert "default_ai_id" not in router_info
                assert "max_context_length" not in router_info

            async with session.get(f"{base_url}/routers") as resp:
                assert resp.status == 200
                routers = await resp.json()
                assert len(routers) == 2
                assert routers[1]["upstreams"] == [
                    {
                        "backend_type": "router",
                        "backend_id": fallback_id,
                        "description": "resilient general tasks",
                    }
                ]

            async with session.delete(f"{base_url}/routers/{fallback_id}") as resp:
                assert resp.status == 409
                assert router_id in (await resp.json())["error"]

            workspace = await _make_workspace(str(tmp_path))
            async with session.post(
                f"{base_url}/sessions",
                json={
                    "backend_type": "router",
                    "backend_id": router_id,
                    "workspace": workspace,
                },
            ) as resp:
                assert resp.status == 201
                data = await resp.json()
                assert data["backend_type"] == "router"
                assert data["backend_id"] == router_id
                session_id = data["id"]

            async with session.get(f"{base_url}/sessions") as resp:
                assert resp.status == 200
                items = await resp.json()
                assert len(items) == 1

            async with session.delete(f"{base_url}/sessions/{session_id}") as resp:
                assert resp.status == 200

            async with session.delete(f"{base_url}/routers/{router_id}") as resp:
                assert resp.status == 200

            async with session.delete(f"{base_url}/routers/{fallback_id}") as resp:
                assert resp.status == 200

            async with session.delete(f"{base_url}/ais/{upstream_ai_id}") as resp:
                assert resp.status == 200

            async with session.delete(f"{base_url}/ais/{aggregator_ai_id}") as resp:
                assert resp.status == 200

    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_rest_errors(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = await create_app(aim, sm, TitleManager())
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

    await aim.create(
        provider="openai",
        model="test",
        api_key="k",
        base_url=mock_base_url,
        id="gw-ai",
    )

    workspace = await _make_workspace(str(tmp_path))
    await sm.create(ai_id="gw-ai", workspace=workspace, id="gw-sess")

    app = await create_app(aim, sm, TitleManager())
    base_url, runner = await _start_app_on_free_port(app)

    try:
        # regression: non-dict JSON body → 400 (R2)
        timeout = ClientTimeout(total=10)
        async with (
            ClientSession(timeout=timeout) as session,
            session.post(
                f"{base_url}/sessions/gw-sess/chat",
                json=[],
            ) as resp,
        ):
            assert resp.status == 400

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
async def test_gateway_blob_send(tmp_path: str, mock_ai_server: MockAIServer, monkeypatch: pytest.MonkeyPatch) -> None:
    # Inbound blobs land in ~/Downloads/.psi/, so redirect Path.home() to keep
    # the run from littering the developer's real Downloads folder.
    monkeypatch.setattr(Path, "home", lambda: Path(str(tmp_path)))
    data_dir = tempfile.mkdtemp(dir=str(tmp_path), prefix="gwb")
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

    await aim.create(
        provider="openai",
        model="test",
        api_key="k",
        base_url=mock_base_url,
        id="gw-ai",
    )

    workspace = await _make_workspace(str(tmp_path))
    await sm.create(ai_id="gw-ai", workspace=workspace, id="gw-sess")

    app = await create_app(aim, sm, TitleManager())
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
    icon_dir = tempfile.mkdtemp(dir=str(tmp_path), prefix="gwfav")
    icon_path = icon_dir + "/icon.png"
    icon_bytes = b"\x89PNG\r\n\x1a\n-fake-favicon-bytes"
    await anyio.Path(icon_path).write_bytes(icon_bytes)

    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)

    app_with = await create_app(aim, sm, TitleManager(), favicon_path=icon_path)
    base_with, runner_with = await _start_app_on_free_port(app_with)

    app_without = await create_app(aim, sm, TitleManager())
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
async def test_gateway_spa_index_app_name() -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = await create_app(aim, sm, TitleManager(), app_name="Haitun Agent")
    base, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session, session.get(f"{base}/spa/index.html") as resp:
            assert resp.status == 200
            body = await resp.text()
            assert "<title>Haitun Agent</title>" in body
    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_gateway_ui_attention() -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    attention = AttentionHub()
    tray = MagicMock()
    attention.bind(tray=tray)
    app = await create_app(aim, sm, TitleManager(), attention=attention)
    base, runner = await _start_app_on_free_port(app)

    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session, session.post(f"{base}/ui/attention") as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body == {"ok": True}
        tray.request_attention.assert_called_once()
    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)
