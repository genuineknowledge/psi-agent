from __future__ import annotations

from typing import Any

import pytest
from aiohttp import ClientSession, web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent.router.models import RouteDecision, Upstream
from psi_agent.router.selector import RouterSelectionError
from psi_agent.router.server import ROUTER_SETTINGS_KEY, RouterSettings, handle_router_chat_completions

SSE_BYTES = b'data: {"id":"x","choices":[{"index":0,"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'


async def _start_app(handler: Handler, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
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


def _settings(*, selected_socket: str, default_socket: str) -> RouterSettings:
    return RouterSettings(
        targets=(
            Upstream(selected_socket, "simple"),
            Upstream(selected_socket, "complex"),
        ),
        router_model="router-model",
        router_base_url="http://router.invalid/v1",
        router_api_key="secret",
        default_socket=default_socket,
        router_timeout=None,
        context_chars=12_000,
        log_details=False,
    )


@pytest.mark.anyio
async def test_semantic_selection_preserves_model_and_body(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port_factory,
) -> None:
    upstream_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()
    received: list[dict[str, Any]] = []

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        return web.Response(body=SSE_BYTES, content_type="text/event-stream")

    async def fake_select_upstream(**kwargs: Any) -> RouteDecision:
        return RouteDecision(candidate=1, reason="complex")

    monkeypatch.setattr("psi_agent.router.server.select_upstream", fake_select_upstream)
    upstream_runner = await _start_app(upstream_handler, upstream_port)
    addr = f"http://127.0.0.1:{upstream_port}"
    router_runner = await _start_router(_settings(selected_socket=addr, default_socket=addr), router_port)
    body = {
        "model": "original",
        "messages": [{"role": "user", "content": "analyze code"}],
        "tools": [{"type": "function"}],
        "unknown_extension": {"keep": True},
    }
    try:
        async with (
            ClientSession() as session,
            session.post(f"http://127.0.0.1:{router_port}/chat/completions", json=body) as response,
        ):
            assert response.status == 200
            assert await response.read() == SSE_BYTES
        assert received == [body]
    finally:
        await router_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.anyio
async def test_selection_failure_uses_default_and_preserves_original_model(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port_factory,
) -> None:
    default_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()
    received: list[dict[str, Any]] = []

    async def default_handler(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        return web.Response(body=SSE_BYTES, content_type="text/event-stream")

    async def failing_select_upstream(**kwargs: Any) -> RouteDecision:
        raise RouterSelectionError("broken decision")

    monkeypatch.setattr("psi_agent.router.server.select_upstream", failing_select_upstream)
    default_runner = await _start_app(default_handler, default_port)
    default_socket = f"http://127.0.0.1:{default_port}"
    router_runner = await _start_router(
        _settings(selected_socket="http://unused", default_socket=default_socket), router_port
    )
    body = {"model": "original", "messages": [{"role": "user", "content": "task"}]}
    try:
        async with (
            ClientSession() as session,
            session.post(f"http://127.0.0.1:{router_port}/chat/completions", json=body) as response,
        ):
            assert response.status == 200
            await response.read()
        assert received == [body]
    finally:
        await router_runner.cleanup()
        await default_runner.cleanup()


@pytest.mark.anyio
async def test_missing_user_context_skips_selector_and_uses_default(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port_factory,
) -> None:
    default_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()
    received: list[dict[str, Any]] = []

    async def default_handler(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        return web.Response(body=SSE_BYTES, content_type="text/event-stream")

    async def forbidden_select_upstream(**kwargs: Any) -> RouteDecision:
        pytest.fail("selector must not be called without user context")

    monkeypatch.setattr("psi_agent.router.server.select_upstream", forbidden_select_upstream)
    default_runner = await _start_app(default_handler, default_port)
    default_socket = f"http://127.0.0.1:{default_port}"
    router_runner = await _start_router(
        _settings(selected_socket="http://unused", default_socket=default_socket), router_port
    )
    body = {"model": "original", "messages": [{"role": "system", "content": "rules"}]}
    try:
        async with (
            ClientSession() as session,
            session.post(f"http://127.0.0.1:{router_port}/chat/completions", json=body) as response,
        ):
            assert response.status == 200
            await response.read()
        assert received == [body]
    finally:
        await router_runner.cleanup()
        await default_runner.cleanup()


@pytest.mark.anyio
async def test_non_object_request_returns_400(unused_tcp_port: int) -> None:
    router_runner = await _start_router(
        _settings(selected_socket="http://unused", default_socket="http://unused"), unused_tcp_port
    )
    try:
        async with (
            ClientSession() as session,
            session.post(f"http://127.0.0.1:{unused_tcp_port}/chat/completions", json=[]) as response,
        ):
            payload = await response.json()
            assert response.status == 400
            assert payload["error"]["code"] == 400
    finally:
        await router_runner.cleanup()


@pytest.mark.anyio
async def test_business_upstream_http_error_returns_502(unused_tcp_port_factory) -> None:
    upstream_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()

    async def upstream_handler(request: web.Request) -> web.Response:
        return web.Response(status=401, text="denied")

    upstream_runner = await _start_app(upstream_handler, upstream_port)
    addr = f"http://127.0.0.1:{upstream_port}"
    router_runner = await _start_router(_settings(selected_socket=addr, default_socket=addr), router_port)
    try:
        async with (
            ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{router_port}/chat/completions",
                json={"model": "original", "messages": []},
            ) as response,
        ):
            payload = await response.json()
            assert response.status == 502
            assert payload["error"]["code"] == 502
            assert "denied" in payload["error"]["message"]
    finally:
        await router_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.anyio
async def test_upstream_disconnect_after_prepare_emits_error_chunk(unused_tcp_port_factory) -> None:
    upstream_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"id":"partial"}\n\n')
        transport = request.transport
        assert transport is not None
        transport.close()
        return response

    upstream_runner = await _start_app(upstream_handler, upstream_port)
    addr = f"http://127.0.0.1:{upstream_port}"
    router_runner = await _start_router(_settings(selected_socket=addr, default_socket=addr), router_port)
    try:
        async with (
            ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{router_port}/chat/completions",
                json={"model": "original", "messages": []},
            ) as response,
        ):
            content = await response.text()
            assert response.status == 200
            assert '"finish_reason": "error"' in content
    finally:
        await router_runner.cleanup()
        await upstream_runner.cleanup()


@pytest.mark.anyio
async def test_router_summary_logs_only_reason_and_final_result(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port_factory,
) -> None:
    upstream_port = unused_tcp_port_factory()
    router_port = unused_tcp_port_factory()

    async def upstream_handler(request: web.Request) -> web.Response:
        return web.Response(body=SSE_BYTES, content_type="text/event-stream")

    async def fake_select_upstream(**kwargs: Any) -> RouteDecision:
        return RouteDecision(candidate=1, reason="requires code analysis")

    monkeypatch.setattr("psi_agent.router.server.select_upstream", fake_select_upstream)
    upstream_runner = await _start_app(upstream_handler, upstream_port)
    addr = f"http://127.0.0.1:{upstream_port}"
    settings = _settings(selected_socket=addr, default_socket=addr)
    settings = RouterSettings(
        targets=settings.targets,
        router_model=settings.router_model,
        router_base_url=settings.router_base_url,
        router_api_key=settings.router_api_key,
        default_socket=settings.default_socket,
        router_timeout=settings.router_timeout,
        context_chars=settings.context_chars,
        log_details=True,
    )
    router_runner = await _start_router(settings, router_port)
    logs: list[str] = []
    sink_id = logger.add(lambda message: logs.append(str(message)), format="{message}", level="INFO")
    try:
        async with (
            ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{router_port}/chat/completions",
                json={"model": "original", "messages": [{"role": "user", "content": "analyze code"}]},
            ) as response,
        ):
            await response.read()
        router_logs = [message.strip() for message in logs if message.startswith("Router ")]
        assert router_logs == [
            "Router reason: requires code analysis",
            f"Router result: socket='{addr}'",
        ]
        assert "description" not in "\n".join(router_logs)
        assert "context_chars" not in "\n".join(router_logs)
        assert "source" not in "\n".join(router_logs)
    finally:
        logger.remove(sink_id)
        await router_runner.cleanup()
        await upstream_runner.cleanup()
