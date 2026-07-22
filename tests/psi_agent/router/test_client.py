from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from aiohttp import web

from psi_agent.router.client import RouterClient, RouterUpstreamError


async def _serve(handler: web.Handler) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []
    assert sockets
    port = sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def _sse_response(request: web.Request, lines: list[bytes]) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for line in lines:
        await response.write(line)
    await response.write_eof()
    return response


@pytest.mark.anyio
async def test_complete_accumulates_content_skips_heartbeats_and_stops_at_done() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [
                b'data: {"choices": []}\n\n',
                b'data: {"choices": [{"delta": {"content": "hel"}, "finish_reason": null}]}\n\n',
                b'data: {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ],
        )

    async for server_url in _serve(handler):
        result = await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)

    assert result.content == "hello"
    assert result.finish_reason == "stop"
    assert result.tool_calls == []


@pytest.mark.anyio
async def test_complete_accumulates_fragmented_tool_calls_in_numeric_index_order() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        first = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "b",
                                "type": "function",
                                "function": {"name": "beta", "arguments": "{"},
                            },
                            {
                                "index": 0,
                                "id": "a",
                                "type": "function",
                                "function": {"name": "alpha", "arguments": '{"x":'},
                            },
                        ]
                    }
                }
            ]
        }
        second = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": "1}"}},
                            {"index": 1, "function": {"arguments": "}"}},
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        lines = [f"data: {json.dumps(first)}\n\n".encode(), f"data: {json.dumps(second)}\n\n".encode()]
        return await _sse_response(request, lines)

    async for server_url in _serve(handler):
        result = await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        {"id": "a", "type": "function", "function": {"name": "alpha", "arguments": '{"x":1}'}},
        {"id": "b", "type": "function", "function": {"name": "beta", "arguments": "{}"}},
    ]


@pytest.mark.anyio
async def test_complete_tolerates_malformed_json_before_valid_finish() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        lines = [
            b"data: {broken}\n\n",
            b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}\n\n',
        ]
        return await _sse_response(request, lines)

    async for server_url in _serve(handler):
        result = await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)

    assert result.content == "ok"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 503])
async def test_complete_raises_for_non_200_response(status: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=status, text="upstream unavailable")

    async for server_url in _serve(handler):
        with pytest.raises(RouterUpstreamError, match=str(status)):
            await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_complete_raises_for_multiple_choices() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(request, [b'data: {"choices": [{"delta": {}}, {"delta": {}}]}\n\n'])

    async for server_url in _serve(handler):
        with pytest.raises(RouterUpstreamError, match="exactly 1 choice"):
            await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_complete_raises_if_stream_lacks_finish_reason() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        lines = [
            b'data: {"choices": [{"delta": {"content": "unfinished"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        return await _sse_response(request, lines)

    async for server_url in _serve(handler):
        with pytest.raises(RouterUpstreamError, match="finish reason"):
            await RouterClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_complete_strips_internal_routing_and_model_but_preserves_other_parameters() -> None:
    received_body: dict[str, object] = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal received_body
        payload = await request.json()
        assert isinstance(payload, dict)
        received_body = payload
        return await _sse_response(request, [b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'])

    body: dict[str, object] = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.4,
        "unknown_parameter": {"enabled": True},
        "routing": {"session_id": "private"},
        "model": "private-model",
    }
    async for server_url in _serve(handler):
        await RouterClient().complete(socket=server_url, body=body, timeout=None)

    assert received_body == {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.4,
        "unknown_parameter": {"enabled": True},
    }
    assert "routing" in body
    assert "model" in body


@pytest.mark.anyio
async def test_stream_raw_preserves_bytes_and_closes_response_after_early_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    class Content:
        async def iter_any(self) -> AsyncIterator[bytes]:
            yield b"data: first\n\n"
            yield b"data: second\n\n"

    class Response:
        status = 200
        content = Content()

        def close(self) -> None:
            nonlocal closed
            closed = True

    class Session:
        async def post(self, endpoint: str, *, json: dict[str, object]) -> Response:
            return Response()

        async def close(self) -> None:
            return None

    monkeypatch.setattr("psi_agent.router.client.aiohttp.ClientSession", lambda **kwargs: Session())

    stream = RouterClient().stream_raw(
        socket="http://127.0.0.1:8080", body={"messages": [], "stream": True}, timeout=None
    )
    async for chunk in stream:
        assert chunk == b"data: first\n\n"
        break
    await stream.aclose()

    assert closed


@pytest.mark.anyio
async def test_stream_raw_strips_internal_routing_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    received_body: dict[str, object] = {}

    class Content:
        async def iter_any(self) -> AsyncIterator[bytes]:
            yield b"data: done\n\n"

    class Response:
        status = 200
        content = Content()

        def close(self) -> None:
            return None

    class Session:
        async def post(self, endpoint: str, *, json: dict[str, object]) -> Response:
            nonlocal received_body
            received_body = json
            return Response()

        async def close(self) -> None:
            return None

    monkeypatch.setattr("psi_agent.router.client.aiohttp.ClientSession", lambda **kwargs: Session())
    body: dict[str, object] = {
        "messages": [],
        "tools": [],
        "custom": "preserved",
        "routing": {"session_id": "private"},
        "model": "private-model",
    }
    chunks = [
        chunk async for chunk in RouterClient().stream_raw(socket="http://127.0.0.1:8080", body=body, timeout=None)
    ]

    assert chunks == [b"data: done\n\n"]
    assert received_body == {"messages": [], "tools": [], "custom": "preserved"}
    assert "routing" in body
    assert "model" in body


@pytest.mark.anyio
async def test_stream_raw_raises_before_yielding_non_200_response() -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=502, text="bad gateway")

    async for server_url in _serve(handler):
        with pytest.raises(RouterUpstreamError, match="502"):
            async for _chunk in RouterClient().stream_raw(
                socket=server_url, body={"messages": [], "stream": True}, timeout=None
            ):
                pass
