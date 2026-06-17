from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from tests.integration.conftest import read_sse


@pytest.mark.anyio
async def test_cli_session_socket_not_exists(tmp_path: Path) -> None:
    """CLI should print error when session socket doesn't exist."""
    result = await anyio.run_process(
        [
            "uv",
            "run",
            "psi-agent",
            "channel",
            "cli",
            "--session-socket",
            str(tmp_path / "nonexistent.sock"),
            "--message",
            "hello",
        ],
        check=False,
    )
    assert result.returncode != 0
    combined = (result.stdout.decode() if result.stdout else "") + (result.stderr.decode() if result.stderr else "")
    assert "Error" in combined or "error" in combined.lower()


@pytest.mark.anyio
async def test_channel_client_handles_non_200(tmp_path: Path) -> None:
    """Channel client should handle non-200 error responses."""
    channel_socket = tmp_path / "busy.sock"

    async def busy_handler(request: web.Request) -> web.StreamResponse:
        return web.json_response(
            {"error": {"message": "Session busy", "type": "session_busy", "code": "busy"}},
            status=503,
        )

    app = web.Application()
    app.router.add_post("/chat/completions", busy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(channel_socket))
    await site.start()

    try:
        await anyio.sleep(0.2)
        timeout = ClientTimeout(total=5)
        connector = UnixConnector(path=str(channel_socket))
        async with (
            ClientSession(connector=connector, timeout=timeout) as session,
            session.post(
                "http://localhost/chat/completions",
                json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            assert resp.status == 503
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_cli_empty_message(tmp_path: Path) -> None:
    """Session should handle empty message from client."""
    channel_socket = tmp_path / "empty.sock"

    async def handler(request: web.Request) -> web.StreamResponse:
        await request.json()
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(channel_socket))
    await site.start()

    try:
        await anyio.sleep(0.2)
        chunks = await read_sse(str(channel_socket), "")
        assert isinstance(chunks, list)
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_cli_handles_session_non_200_error(tmp_path: Path) -> None:
    """CLI should print error message and exit code 1 when session returns non-200."""
    channel_socket = tmp_path / "error.sock"

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": {"message": "something went wrong"}}, status=500)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(channel_socket))
    await site.start()

    try:
        await anyio.sleep(0.2)
        result = await anyio.run_process(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "hello"],
            check=False,
        )
        assert result.returncode != 0, f"Expected non-zero exit, got {result.returncode}"
        output = result.stdout.decode() if result.stdout else ""
        assert "Error" in output or "something went wrong" in output, f"Got: {output[:200]}"
    finally:
        await runner.cleanup()
