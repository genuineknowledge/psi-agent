from __future__ import annotations

import json
import socket as _s

import pytest
from aiohttp import web

from psi_agent.session.ai_client import AiClient


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
async def test_ai_client_yields_usage_before_tool_terminal_finish() -> None:
    """A tool run must account for provider usage before executing tools."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        terminal = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        usage = {
            "choices": [{"delta": {}, "finish_reason": "usage"}],
            "psi_usage": {"prompt_tokens": 101, "completion_tokens": 7, "total_tokens": 108},
        }
        compaction = {
            "choices": [{"delta": {}, "finish_reason": "compaction_needed"}],
            "psi_compaction": {"needed": True, "prompt_tokens": 101, "threshold": 100},
        }
        await resp.write(f"data: {json.dumps(terminal)}\n\n".encode())
        await resp.write(f"data: {json.dumps(usage)}\n\n".encode())
        await resp.write(f"data: {json.dumps(compaction)}\n\n".encode())
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
        deltas = [delta async for delta in client.stream({"messages": [], "stream": True})]
        assert [delta.finish_reason for delta in deltas] == ["usage", "compaction_needed", "tool_calls"]
        assert (deltas[0].input_tokens, deltas[0].output_tokens) == (101, 7)
        assert deltas[1].compaction_needed
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_releases_tool_terminal_before_next_business_frame() -> None:
    """A provider without usage must retain the original tool-call boundary."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        terminal = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        next_response = {"choices": [{"delta": {"content": "next"}, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(terminal)}\n\n".encode())
        await resp.write(f"data: {json.dumps(next_response)}\n\n".encode())
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
        deltas = [delta async for delta in client.stream({"messages": [], "stream": True})]
        assert [delta.finish_reason for delta in deltas] == ["tool_calls"]
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_client_rejects_malformed_usage_counts() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        usage = {
            "choices": [{"delta": {}, "finish_reason": "usage"}],
            "psi_usage": {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 0},
        }
        await resp.write(f"data: {json.dumps(usage)}\n\n".encode())
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
        deltas = [delta async for delta in client.stream({"messages": [], "stream": True})]
        assert len(deltas) == 1
        assert deltas[0].input_tokens is None
        assert deltas[0].output_tokens is None
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
async def test_ai_client_data_without_space_and_empty_payload():
    """`data:` needs no space after the colon, and empty payloads are heartbeats.

    Guards the shared ``parse_sse_data`` wiring: the old ``startswith("data: ")``
    guard dropped space-less frames whole, and an empty ``data:`` must be
    skipped silently rather than reaching ``json.loads``.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"data:\n\n")
        await resp.write(
            b"data:"
            + json.dumps({"id": "n", "choices": [{"delta": {"content": "nospace"}, "finish_reason": "stop"}]}).encode()
            + b"\n\n"
        )
        await resp.write(b"data:[DONE]\n\n")
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
        assert deltas[0].content == "nospace"
        assert deltas[0].finish_reason == "stop"
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


@pytest.mark.anyio
async def test_ai_client_choices_not_a_list():
    """Malformed choices (not a list) → skipped, stream continues."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices": "not_a_list"}\n\n')
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


@pytest.mark.anyio
async def test_ai_client_choice_not_a_dict():
    """Malformed choice (not a dict) → skipped, stream continues."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices": [42]}\n\n')
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
