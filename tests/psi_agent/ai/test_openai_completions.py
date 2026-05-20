from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, UnixConnector, web

from psi_agent.ai.openai_completions import OpenAICompletions


def test_cli_dataclass_defaults() -> None:
    config = OpenAICompletions(
        session_socket="/tmp/test.sock",
        model="gpt-test",
        api_key="sk-test",
    )
    assert config.base_url == "https://api.openai.com/v1"
    assert config.verbose is False


@pytest.mark.anyio
async def test_server_streaming_response(tmp_path: Path) -> None:
    socket_path = tmp_path / "ai.sock"

    async def mock_upstream_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body["model"] == "gpt-test"
        assert body["stream"] is True

        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        chunk = json.dumps(
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Hello from AI"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    mock_app = web.Application()
    mock_app.router.add_post("/v1/chat/completions", mock_upstream_handler)

    runner = web.AppRunner(mock_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    port: int = site._server.sockets[0].getsockname()[1]  # ty: ignore[unresolved-attribute]

    try:
        config = OpenAICompletions(
            session_socket=str(socket_path),
            model="gpt-test",
            api_key="sk-test",
            base_url=f"http://127.0.0.1:{port}/v1",
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(config.run)
            await anyio.sleep(0.2)

            connector = UnixConnector(path=str(socket_path))
            async with ClientSession(connector=connector) as session:
                req_data = {
                    "model": "gpt-test",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                }
                async with session.post("http://localhost/v1/chat/completions", json=req_data) as resp:
                    assert resp.status == 200
                    chunks: list[str] = []
                    async for raw in resp.content:
                        chunk = raw.decode().strip()
                        if chunk.startswith("data: ") and chunk != "data: [DONE]":
                            chunks.append(chunk)
                    assert len(chunks) > 0
                    all_data = "".join(chunks)
                    assert "Hello from AI" in all_data

            tg.cancel_scope.cancel()

    finally:
        await runner.cleanup()
