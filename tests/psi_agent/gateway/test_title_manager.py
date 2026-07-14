from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent._sockets import create_site
from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_manager_set_get() -> None:
    tm = TitleManager()
    await tm.set("s1", "Title 1")
    assert tm.get_all() == {"s1": "Title 1"}


@pytest.mark.anyio
async def test_title_manager_persist_on_set() -> None:
    persist_called = 0

    async def fake_persist():
        nonlocal persist_called
        persist_called += 1

    tm = TitleManager(_persist=fake_persist)
    await tm.set("s1", "Title 1")
    assert persist_called == 1


@pytest.mark.anyio
async def test_title_manager_generate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200)
        await resp.prepare(request)
        chunk = {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Generated Title"},
                    "finish_reason": "stop",
                }
            ]
        }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()

    socket_path = str(tmp_path / "ai.sock")
    site = create_site(runner, socket_path)
    await site.start()

    try:
        persist_called = 0

        async def fake_persist():
            nonlocal persist_called
            persist_called += 1

        tm = TitleManager(_persist=fake_persist)
        title = await tm.generate("s1", socket_path, "user", "assistant")

        assert title == "Generated Title"
        assert tm.get_all()["s1"] == "Generated Title"
        assert persist_called == 1
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_title_manager_generate_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=500, text="AI Error")

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()

    socket_path = str(tmp_path / "ai_error.sock")
    site = create_site(runner, socket_path)
    await site.start()

    try:
        tm = TitleManager()
        title = await tm.generate("s1", socket_path, "user", "assistant")
        assert title is None
        assert "s1" not in tm.get_all()
    finally:
        await runner.cleanup()
