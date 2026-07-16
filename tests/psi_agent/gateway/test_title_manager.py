from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_manager_set_and_get() -> None:
    persisted = False

    async def _persist() -> None:
        nonlocal persisted
        persisted = True

    tm = TitleManager(_persist=_persist)
    await tm.set("s1", "Title 1")
    assert tm.get_all() == {"s1": "Title 1"}
    assert persisted is True


@pytest.mark.anyio
async def test_title_manager_generate_success(tmp_path: Path) -> None:
    tm = TitleManager()
    socket_path = str(tmp_path / "ai.sock")

    async def handle_ai(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "Generated Title"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handle_ai)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    try:
        title = await tm.generate("s1", socket_path, "hi", "hello")
        assert title == "Generated Title"
        assert tm.get_all() == {"s1": "Generated Title"}
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_title_manager_generate_failure(tmp_path: Path) -> None:
    tm = TitleManager()
    socket_path = str(tmp_path / "ai_fail.sock")

    async def handle_ai(request: web.Request) -> web.Response:
        return web.Response(status=500)

    app = web.Application()
    app.router.add_post("/chat/completions", handle_ai)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    try:
        title = await tm.generate("s1", socket_path, "hi", "hello")
        assert title is None
        assert tm.get_all() == {}
    finally:
        await runner.cleanup()
