from __future__ import annotations

import json
import socket as _s

import pytest
from aiohttp import web

from psi_agent.session._ai_client import AiClient


@pytest.mark.anyio
async def test_ai_client_simple_content():
    """AiClient yields AiDelta with content and finish_reason from SSE."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for data in [
            json.dumps({"id": "0", "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}),
            json.dumps({"id": "1", "choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]}),
        ]:
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) >= 2
        contents = [d.content or "" for d in deltas]
        assert "Hello" in "".join(contents)
        assert "world" in "".join(contents)
        assert deltas[-1].finish_reason == "stop"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_tool_calls():
    """AiClient passes through partial tool_calls without accumulation."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        tc_chunk = {
            "id": "t",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": '{"city":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        await resp.write(f"data: {json.dumps(tc_chunk)}\n\n".encode())
        tc_chunk2 = {
            "id": "t2",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"Beijing"}'}}]},
                    "finish_reason": "tool_calls",
                }
            ],
        }
        await resp.write(f"data: {json.dumps(tc_chunk2)}\n\n".encode())
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) >= 2
        assert deltas[-1].finish_reason == "tool_calls"
        tc_list = deltas[0].tool_calls or []
        assert len(tc_list) == 1
        assert tc_list[0]["function"]["name"] == "get_weather"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_non_200():
    """Non-200 response yields AiDelta with finish_reason='error'."""

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.json_response({"error": "bad request"}, status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "error"
        assert "400" in (deltas[0].content or "")
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_multi_choice_error():
    """Multiple choices (>1) yields AiDelta with finish_reason='error'."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "x", "choices": [{"delta": {}}, {"delta": {}}]}
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "error"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_empty_choices_skipped():
    """0 choices -> heartbeat, skipped (no AiDelta yielded)."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"id":"h","choices":[]}\n\n')
        await resp.write(
            b"data: "
            + json.dumps({"id": "r", "choices": [{"delta": {"content": "real"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "real"
        assert deltas[0].finish_reason == "stop"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_non_data_sse_skipped():
    """SSE lines not starting with 'data: ' are skipped."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b":comment\n")
        await resp.write(b"event: ping\ndata: {}\n\n")
        await resp.write(
            b"data: "
            + json.dumps({"id": "t", "choices": [{"delta": {"content": "real"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "real"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_reasoning_field():
    """AiClient yields AiDelta with reasoning from SSE."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {
            "id": "r",
            "choices": [{"delta": {"reasoning": "Let me think..."}, "finish_reason": "stop"}],
        }
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].reasoning == "Let me think..."
        assert deltas[0].finish_reason == "stop"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_null_delta_converted():
    """When delta is null (not a dict), it's treated as empty dict."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "x", "choices": [{"delta": None, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(data)}\n\n".encode())
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].finish_reason == "stop"
        assert deltas[0].content is None
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_malformed_json_skipped():
    """Malformed JSON in SSE data line is skipped with no crash."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data: not json\n\n")
        await resp.write(
            b"data: "
            + json.dumps({"id": "g", "choices": [{"delta": {"content": "good"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
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
        client = AiClient(ai_socket=f"http://127.0.0.1:{port}")
        deltas = [d async for d in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].content == "good"
    finally:
        await runner.cleanup()
