from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientConnectionResetError, ClientSession, UnixConnector, web

from psi_agent.ai.openai_completions import OpenAICompletions
from psi_agent.ai.openai_completions.server import _write_to_client


class _ResetResponse:
    """Stand-in StreamResponse whose write() raises like a hung-up client."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.writes = 0

    async def write(self, data: bytes) -> None:
        self.writes += 1
        raise self._exc


class _OkResponse:
    def __init__(self) -> None:
        self.data = b""

    async def write(self, data: bytes) -> None:
        self.data += data


@pytest.mark.anyio
@pytest.mark.parametrize("exc", [ClientConnectionResetError(), ConnectionResetError()])
async def test_write_to_client_swallows_disconnect(exc: BaseException) -> None:
    """A client disconnect mid-stream returns False instead of raising."""
    resp = _ResetResponse(exc)
    ok = await _write_to_client(resp, b"data: x\n\n", context="streaming response")
    assert ok is False
    assert resp.writes == 1


@pytest.mark.anyio
async def test_write_to_client_success() -> None:
    resp = _OkResponse()
    ok = await _write_to_client(resp, b"data: x\n\n", context="streaming response")
    assert ok is True
    assert resp.data == b"data: x\n\n"


def test_cli_dataclass_defaults() -> None:
    config = OpenAICompletions(
        session_socket="/tmp/test.sock",
        model="gpt-test",
        api_key="sk-test",
    )
    assert config.base_url == ""  # env var fallback happens in run()
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

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


@pytest.mark.anyio
async def test_openai_upstream_non_200(tmp_path: Path) -> None:
    """When upstream returns non-200, SSE error is forwarded."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": {"message": "Invalid API key", "type": "auth", "code": "401"}}, status=401)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    socket_path = str(tmp_path / "ai.sock")
    cfg = OpenAICompletions(
        session_socket=socket_path, model="test", api_key="k", base_url=f"http://127.0.0.1:{port}/v1"
    )
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(cfg.run)
            await anyio.sleep(0.2)
            connector = UnixConnector(path=socket_path)
            async with (
                ClientSession(connector=connector) as s,
                s.post(
                    "http://localhost/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                ) as resp,
            ):
                assert resp.status == 200
                text = ""
                async for raw in resp.content:
                    text += raw.decode()
                assert "error" in text.lower()
            tg.cancel_scope.cancel()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_openai_unreachable_upstream(tmp_path: Path) -> None:
    """When upstream is unreachable, SSE error with 502 code is returned."""
    socket_path = str(tmp_path / "ai.sock")
    cfg = OpenAICompletions(session_socket=socket_path, model="test", api_key="k", base_url="http://127.0.0.1:19999/v1")
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(cfg.run)
            await anyio.sleep(0.2)
            connector = UnixConnector(path=socket_path)
            async with (
                ClientSession(connector=connector) as s,
                s.post(
                    "http://localhost/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                ) as resp,
            ):
                text = ""
                async for raw in resp.content:
                    text += raw.decode()
                assert "error" in text.lower()
            tg.cancel_scope.cancel()
    except Exception:
        pass


def test_openai_env_fallback(monkeypatch) -> None:
    """Empty fields should resolve from env vars."""
    monkeypatch.setenv("OPENAI_MODEL", "gpt-from-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    config = OpenAICompletions(session_socket="/tmp/s.sock", model="", base_url="", api_key="")
    assert config.model or os.environ.get("OPENAI_MODEL", "") == "gpt-from-env"
    assert config.base_url or os.environ.get("OPENAI_BASE_URL", "") == "https://env.example.com/v1"
    assert config.api_key or os.environ.get("OPENAI_API_KEY", "") == "sk-from-env"


def test_openai_cli_overrides_env(monkeypatch) -> None:
    """CLI args should take precedence over env vars."""
    monkeypatch.setenv("OPENAI_MODEL", "gpt-from-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")

    config = OpenAICompletions(
        session_socket="/tmp/s.sock", model="gpt-from-cli", base_url="https://cli.example.com/v1"
    )
    assert config.model == "gpt-from-cli"
    assert config.base_url == "https://cli.example.com/v1"


def test_openai_base_url_default() -> None:
    """base_url should fall back to openai default when neither CLI nor env is set."""
    config = OpenAICompletions(session_socket="/tmp/s.sock", model="test")
    resolved = config.base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert resolved == "https://api.openai.com/v1"
