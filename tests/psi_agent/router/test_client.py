from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from psi_agent.router.client import RouterHttpClient
from psi_agent.router.errors import RouterUpstreamError


@asynccontextmanager
async def _serve(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", []) if site._server is not None else []
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
async def test_complete_skips_zero_choice_heartbeats() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [
                b'data: {"choices": []}\n\n',
                b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ],
        )

    async with _serve(handler) as server_url:
        result = await RouterHttpClient().complete(
            socket=server_url, body={"messages": [], "stream": True}, timeout=None
        )

    assert result.content == "ok"
    assert result.finish_reason == "stop"


@pytest.mark.anyio
async def test_buffered_complete_preserves_events_and_compaction_metadata() -> None:
    content_event = {
        "id": "answer",
        "model": "model-a",
        "custom": {"preserved": True},
        "choices": [{"index": 0, "delta": {"content": "ok", "reasoning": "why"}, "finish_reason": "stop"}],
    }
    compaction_event = {
        "id": "answer",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "compaction_needed"}],
        "psi_compaction": {"needed": True, "prompt_tokens": 100, "threshold": 80},
    }
    usage_event = {
        "id": "usage",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "usage"}],
        "psi_usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    }

    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [
                f"data: {json.dumps(content_event)}\n\n".encode(),
                f"data: {json.dumps(usage_event)}\n\n".encode(),
                f"data: {json.dumps(compaction_event)}\n\n".encode(),
                b"data: [DONE]\n\n",
            ],
        )

    async with _serve(handler) as server_url:
        result = await RouterHttpClient().buffered_complete(
            socket=server_url,
            body={"messages": [], "stream": True},
            timeout=None,
        )

    assert result.events == (content_event, usage_event, compaction_event)
    assert result.completion.content == "ok"
    assert result.completion.reasoning == "why"
    assert result.completion.finish_reason == "stop"


@pytest.mark.anyio
async def test_complete_rejects_multiple_choices() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(request, [b'data: {"choices": [{"delta": {}}, {"delta": {}}]}\n\n'])

    async with _serve(handler) as server_url:
        with pytest.raises(RouterUpstreamError, match="exactly one upstream choice"):
            await RouterHttpClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 503])
async def test_complete_raises_for_non_200_response(status: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=status, text="upstream unavailable")

    async with _serve(handler) as server_url:
        with pytest.raises(RouterUpstreamError, match=str(status)):
            await RouterHttpClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_complete_raises_for_upstream_error_finish() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [b'data: {"choices": [{"delta": {"content": "backend failed"}, "finish_reason": "error"}]}\n\n'],
        )

    async with _serve(handler) as server_url:
        with pytest.raises(RouterUpstreamError, match="backend failed"):
            await RouterHttpClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_complete_raises_if_stream_lacks_finish_reason() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [
                b'data: {"choices": [{"delta": {"content": "unfinished"}}]}\n\n',
                b"data: [DONE]\n\n",
            ],
        )

    async with _serve(handler) as server_url:
        with pytest.raises(RouterUpstreamError, match="finish reason"):
            await RouterHttpClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


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
        return await _sse_response(
            request,
            [f"data: {json.dumps(first)}\n\n".encode(), f"data: {json.dumps(second)}\n\n".encode()],
        )

    async with _serve(handler) as server_url:
        result = await RouterHttpClient().complete(
            socket=server_url, body={"messages": [], "stream": True}, timeout=None
        )

    assert result.tool_calls == [
        {"id": "a", "type": "function", "function": {"name": "alpha", "arguments": '{"x":1}'}},
        {"id": "b", "type": "function", "function": {"name": "beta", "arguments": "{}"}},
    ]


@pytest.mark.anyio
async def test_complete_rejects_incomplete_tool_call_even_when_finish_is_stop() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _sse_response(
            request,
            [
                b'data: {"choices":[{"delta":{"tool_calls":'
                b'[{"index":0,"function":{"arguments":"{}"}}]},'
                b'"finish_reason":"stop"}]}\n\n'
            ],
        )

    async with _serve(handler) as server_url:
        with pytest.raises(RouterUpstreamError, match="incomplete tool call"):
            await RouterHttpClient().complete(socket=server_url, body={"messages": [], "stream": True}, timeout=None)


@pytest.mark.anyio
async def test_stream_close_after_one_event_closes_response_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_closed = False
    session_closed = False

    class Content:
        def __init__(self) -> None:
            self.lines = [
                b'data: {"choices": [{"delta": {"content": "first"}, "finish_reason": null}]}\n',
                b"\n",
                b'data: {"choices": [{"delta": {"content": "second"}, "finish_reason": "stop"}]}\n',
                b"\n",
            ]

        async def readline(self) -> bytes:
            return self.lines.pop(0) if self.lines else b""

    class Response:
        status = 200
        content = Content()

        async def text(self) -> str:
            return ""

        def close(self) -> None:
            nonlocal response_closed
            response_closed = True

    class Session:
        async def post(self, endpoint: str, *, json: dict[str, object]) -> Response:
            return Response()

        async def close(self) -> None:
            nonlocal session_closed
            session_closed = True

    monkeypatch.setattr("psi_agent.router.client.aiohttp.ClientSession", lambda **kwargs: Session())
    stream = RouterHttpClient().stream(
        socket="http://127.0.0.1:8080", body={"messages": [], "stream": True}, timeout=None
    )

    event = await anext(stream)
    await stream.aclose()

    assert event["choices"][0]["delta"]["content"] == "first"
    assert response_closed
    assert session_closed
