from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, UnixConnector, web

from psi_agent.ai.anthropic_messages import AnthropicMessages


def test_cli_dataclass() -> None:
    config = AnthropicMessages(
        session_socket="/tmp/test.sock",
        model="claude-sonnet",
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com",
    )
    assert config.model == "claude-sonnet"
    assert config.verbose is False


@pytest.mark.anyio
async def test_anthropic_thinking_conversion(tmp_path: Path) -> None:
    socket_path = tmp_path / "ai.sock"

    async def mock_anthropic_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body["model"] == "claude-sonnet"

        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        event_data = json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            }
        )
        await resp.write(f"event: content_block_start\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Let me think about this..."},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " The answer is 42."},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    mock_app = web.Application()
    mock_app.router.add_post("/v1/messages", mock_anthropic_handler)

    runner = web.AppRunner(mock_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    host, port, *_ = site._server.sockets[0].getsockname()  # ty: ignore[unresolved-attribute]

    try:
        config = AnthropicMessages(
            session_socket=str(socket_path),
            model="claude-sonnet",
            api_key="sk-ant-test",
            base_url=f"http://{host}:{port}/v1",
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(config.run)
            await anyio.sleep(0.2)

            connector = UnixConnector(path=str(socket_path))
            async with ClientSession(connector=connector) as session:
                req_data = {
                    "model": "claude-sonnet",
                    "messages": [{"role": "user", "content": "what is 6*7"}],
                    "stream": True,
                }
                async with session.post("http://localhost/v1/chat/completions", json=req_data) as resp:
                    assert resp.status == 200
                    chunks: list[str] = []
                    async for raw in resp.content:
                        chunk = raw.decode().strip()
                        if chunk.startswith("data: ") and chunk != "data: [DONE]":
                            chunks.append(chunk)
                    all_text = "".join(chunks)
                    assert "reasoning_content" in all_text
                    assert "Let me think" in all_text
                    assert "The answer is 42" in all_text

            tg.cancel_scope.cancel()

    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_anthropic_tool_use_conversion(tmp_path: Path) -> None:
    socket_path = tmp_path / "ai.sock"

    async def mock_anthropic_handler(request: web.Request) -> web.StreamResponse:
        _body = await request.json()  # verify parsing succeeds
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        event_data = json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool_1", "name": "bash", "input": {}},
            }
        )
        await resp.write(f"event: content_block_start\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls"}'},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    mock_app = web.Application()
    mock_app.router.add_post("/v1/messages", mock_anthropic_handler)

    runner = web.AppRunner(mock_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    host, port, *_ = site._server.sockets[0].getsockname()  # ty: ignore[unresolved-attribute]

    try:
        config = AnthropicMessages(
            session_socket=str(socket_path),
            model="claude-sonnet",
            api_key="sk-ant-test",
            base_url=f"http://{host}:{port}/v1",
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(config.run)
            await anyio.sleep(0.2)

            connector = UnixConnector(path=str(socket_path))
            async with ClientSession(connector=connector) as session:
                req_data = {
                    "model": "claude-sonnet",
                    "messages": [{"role": "user", "content": "run ls"}],
                    "stream": True,
                }
                async with session.post("http://localhost/v1/chat/completions", json=req_data) as resp:
                    assert resp.status == 200
                    chunks: list[str] = []
                    async for raw in resp.content:
                        chunk = raw.decode().strip()
                        if chunk.startswith("data: ") and chunk != "data: [DONE]":
                            chunks.append(chunk)
                    all_text = "".join(chunks)
                    assert "tool_calls" in all_text or "bash" in all_text

            tg.cancel_scope.cancel()

    finally:
        await runner.cleanup()
