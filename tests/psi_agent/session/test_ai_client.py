from __future__ import annotations

import json
import socket as _s
from typing import TYPE_CHECKING

import pytest
from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    # ``Message`` is stub-only — importing it at runtime raises ImportError.
    from loguru import Message

from psi_agent.session.ai_client import AiClient


class _LoguruSink:
    """Capture loguru records with their level names.

    ``caplog`` sees nothing from loguru — it never reaches the stdlib logging
    handlers — so a negative assertion written against ``caplog`` passes no
    matter what.  Recording the level here is the point: the TTFT probe has to
    be INFO, because production runs at INFO and a DEBUG probe emits nothing.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def __call__(self, message: Message) -> None:
        record = message.record
        self.records.append((record["level"].name, record["message"]))

    def messages_at(self, level: str) -> list[str]:
        return [msg for lvl, msg in self.records if lvl == level]


@pytest.fixture
def loguru_sink():
    sink = _LoguruSink()
    sink_id = logger.add(sink, level="DEBUG")
    try:
        yield sink
    finally:
        logger.remove(sink_id)


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


@pytest.mark.anyio
async def test_ttft_logged_at_info_with_bytes(loguru_sink):
    """TTFT is recorded, at INFO, carrying the request byte count.

    Level is asserted explicitly: DEBUG would emit nothing in production.
    """
    seen_body: dict = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        # Guards the ``json=`` → ``data=`` switch: the body must still arrive as
        # parseable JSON with the right Content-Type, or the byte count would be
        # bought at the cost of a broken request.
        seen_body["raw"] = await request.read()
        seen_body["content_type"] = request.headers.get("Content-Type")
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "a", "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}
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
        request_body = {"messages": [{"role": "user", "content": "中文也要按字节算"}], "stream": True}
        deltas = [d async for d in client.stream(request_body)]
        assert [d.content for d in deltas] == ["hi"]
    finally:
        await runner.cleanup()

    assert seen_body["content_type"] == "application/json"
    assert json.loads(seen_body["raw"]) == request_body

    info_msgs = [m for m in loguru_sink.messages_at("INFO") if "ttft=" in m]
    assert len(info_msgs) == 1, f"expected exactly one INFO ttft line, got {loguru_sink.records}"
    assert "kind=content" in info_msgs[0]
    # Bytes, not characters.  ``json.dumps`` escapes CJK to ``\uXXXX``, so each
    # character of user text costs 6 bytes here — the gap against a character
    # count like ``prompt_budget``'s is wide, and in the wrong direction to
    # ignore.
    expected_bytes = len(json.dumps(request_body).encode())
    assert f"req_bytes={expected_bytes}" in info_msgs[0]
    content_chars = len(request_body["messages"][0]["content"])
    assert expected_bytes > content_chars * 3, "req_bytes must not be a character count"
    # No ttft line may hide at DEBUG.
    assert [m for m in loguru_sink.messages_at("DEBUG") if "ttft=" in m] == []


@pytest.mark.anyio
async def test_ttft_counts_reasoning_and_fires_once(loguru_sink):
    """A leading ``reasoning`` chunk is the first token, and TTFT logs once.

    The card renders thinking live, so keying on ``content`` alone would skip
    past the reasoning chunks here and overstate the user-visible wait.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for delta in [
            {"reasoning": "thinking..."},
            {"reasoning": " more"},
            {"content": "answer"},
        ]:
            chunk = {"id": "r", "choices": [{"delta": delta, "finish_reason": None}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
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
        assert len(deltas) == 3
    finally:
        await runner.cleanup()

    info_msgs = [m for m in loguru_sink.messages_at("INFO") if "ttft=" in m]
    assert len(info_msgs) == 1, f"ttft must fire once per turn, got {info_msgs}"
    assert "kind=reasoning" in info_msgs[0]


@pytest.mark.anyio
async def test_status_line_marks_itself_as_not_first_token(loguru_sink):
    """The status line reports the first-hop header time and says so.

    Reading that line as TTFB is what produced the retracted "upstream 0.18s"
    figure, so it must be distinguishable from the real TTFT line.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        data = {"id": "s", "choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}
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
        _ = [d async for d in client.stream({"messages": [], "stream": True})]
    finally:
        await runner.cleanup()

    status_msgs = [m for m in loguru_sink.messages_at("INFO") if "AI response status" in m]
    assert len(status_msgs) == 1
    assert "非首字" in status_msgs[0]
    assert "ttft=" not in status_msgs[0]
