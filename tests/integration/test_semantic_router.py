from __future__ import annotations

import json
from typing import Any

import pytest
from aiohttp import ClientSession, web
from aiohttp.typedefs import Handler

from psi_agent.router.models import Upstream
from psi_agent.router.server import ROUTER_SETTINGS_KEY, RouterSettings, handle_router_chat_completions

TOOL_SSE = (
    b'data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":'
    b'[{"index":0,"function":{"name":"search","arguments":"{}"}}]}}]}\n\n'
    b"data: [DONE]\n\n"
)


async def _start(handler: Handler, path: str, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_post(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def _start_router(settings: RouterSettings, port: int) -> web.AppRunner:
    app = web.Application()
    app[ROUTER_SETTINGS_KEY] = settings
    app.router.add_post("/chat/completions", handle_router_chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


@pytest.mark.anyio
async def test_semantic_router_selects_models_and_preserves_tool_sse(unused_tcp_port_factory) -> None:
    router_model_port = unused_tcp_port_factory()
    complex_port = unused_tcp_port_factory()
    default_port = unused_tcp_port_factory()
    service_port = unused_tcp_port_factory()
    simple_requests: list[dict[str, Any]] = []
    complex_requests: list[dict[str, Any]] = []
    default_requests: list[dict[str, Any]] = []

    async def router_model_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        rendered = "\n".join(item["content"] for item in body["messages"])
        if "候选模型" not in rendered:
            simple_requests.append(body)
            return web.Response(body=b"data: [DONE]\n\n", content_type="text/event-stream")
        assert "qwen" not in rendered
        assert "deepseek" not in rendered
        assert "127.0.0.1" not in rendered
        context = body["messages"][1]["content"]
        candidate = 1 if "code" in context else 0
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        content = f'{{"candidate":{candidate},"reason":"matched"}}'
        chunk = {"choices": [{"index": 0, "delta": {"content": content}}]}
        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    async def complex_handler(request: web.Request) -> web.Response:
        complex_requests.append(await request.json())
        return web.Response(body=TOOL_SSE, content_type="text/event-stream")

    async def default_handler(request: web.Request) -> web.Response:
        default_requests.append(await request.json())
        return web.Response(body=b"data: [DONE]\n\n", content_type="text/event-stream")

    runners = [
        await _start(router_model_handler, "/chat/completions", router_model_port),
        await _start(complex_handler, "/chat/completions", complex_port),
        await _start(default_handler, "/chat/completions", default_port),
    ]
    settings = RouterSettings(
        targets=(
            Upstream(f"http://127.0.0.1:{router_model_port}", "summaries and simple tasks"),
            Upstream(f"http://127.0.0.1:{complex_port}", "code analysis and reasoning"),
        ),
        router_socket=f"http://127.0.0.1:{router_model_port}",
        default_socket=f"http://127.0.0.1:{default_port}",
        router_timeout=1,
        context_chars=12_000,
    )
    router_runner = await _start_router(settings, service_port)
    try:
        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{service_port}/chat/completions",
                json={"model": "original", "messages": [{"role": "user", "content": "summarize this"}]},
            ) as response:
                assert response.status == 200
                await response.read()
            async with session.post(
                f"http://127.0.0.1:{service_port}/chat/completions",
                json={"model": "original", "messages": [{"role": "user", "content": "analyze code"}]},
            ) as response:
                assert response.status == 200
                assert await response.read() == TOOL_SSE
        assert simple_requests[0]["model"] == "original"
        assert complex_requests[0]["model"] == "original"
        assert default_requests == []
    finally:
        await router_runner.cleanup()
        for runner in runners:
            await runner.cleanup()


@pytest.mark.anyio
async def test_damaged_router_response_uses_default_and_preserves_model(unused_tcp_port_factory) -> None:
    router_model_port = unused_tcp_port_factory()
    default_port = unused_tcp_port_factory()
    service_port = unused_tcp_port_factory()
    default_requests: list[dict[str, Any]] = []

    async def router_model_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"index":0,"delta":{"content":"damaged"}}]}\n\n')
        await response.write(b"data: [DONE]\n\n")
        return response

    async def default_handler(request: web.Request) -> web.Response:
        default_requests.append(await request.json())
        return web.Response(body=b"data: [DONE]\n\n", content_type="text/event-stream")

    runners = [
        await _start(router_model_handler, "/chat/completions", router_model_port),
        await _start(default_handler, "/chat/completions", default_port),
    ]
    settings = RouterSettings(
        targets=(Upstream(f"http://127.0.0.1:{router_model_port}", "simple"),),
        router_socket=f"http://127.0.0.1:{router_model_port}",
        default_socket=f"http://127.0.0.1:{default_port}",
        router_timeout=1,
        context_chars=12_000,
    )
    router_runner = await _start_router(settings, service_port)
    body = {"model": "original", "messages": [{"role": "user", "content": "task"}]}
    try:
        async with (
            ClientSession() as session,
            session.post(f"http://127.0.0.1:{service_port}/chat/completions", json=body) as response,
        ):
            assert response.status == 200
            await response.read()
        assert default_requests == [body]
    finally:
        await router_runner.cleanup()
        for runner in runners:
            await runner.cleanup()
