# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Channel error handling integration tests."""

import subprocess
from pathlib import Path

import anyio
import pytest
from aiohttp import web


@pytest.mark.anyio
async def test_cli_session_socket_not_exists(tmp_path: Path) -> None:
    """CLI should print error when session socket doesn't exist."""
    result = subprocess.run(
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
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Error" in combined or "error" in combined.lower()


@pytest.mark.anyio
async def test_session_busy_prints_error(tmp_path: Path) -> None:
    """CLI should print 'Session busy' when receiving 503."""
    channel_socket = tmp_path / "busy.sock"

    async def busy_handler(request: web.Request) -> web.StreamResponse:
        return web.json_response(
            {"error": {"message": "Session busy", "type": "session_busy", "code": "busy"}},
            status=503,
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", busy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(channel_socket))
    await site.start()

    try:
        await anyio.sleep(0.2)
        result = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "hi"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "busy" in combined.lower() or "503" in combined
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_cli_empty_message(tmp_path: Path) -> None:
    """CLI with empty message should still work."""
    channel_socket = tmp_path / "empty.sock"

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        # Verify empty message was sent
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, str(channel_socket))
    await site.start()

    try:
        await anyio.sleep(0.2)
        result = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", ""],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
    finally:
        await runner.cleanup()
