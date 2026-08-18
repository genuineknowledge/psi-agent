from __future__ import annotations

import json
import socket as _s

import pytest
from aiohttp import web

from psi_agent.gateway._title_manager import TitleManager


@pytest.mark.anyio
async def test_title_manager_set_get_delete() -> None:
    m = TitleManager()
    await m.set("s1", "测试标题")
    assert m.get_all() == {"s1": "测试标题"}
    await m.delete("s1")
    assert m.get_all() == {}
    await m.delete("s1")  # idempotent


@pytest.mark.anyio
async def test_title_manager_generate_success() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = json.dumps({"choices": [{"delta": {"content": "生成的标题"}}]})
        await resp.write(f"data: {data}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        m = TitleManager()
        res = await m.generate("s1", f"http://127.0.0.1:{port}", "Hello", "Hello! How can I help you?")
        assert res == "生成的标题"
        assert m.get_all() == {"s1": "生成的标题"}
    finally:
        await runner.cleanup()
