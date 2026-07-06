from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing

import anyio
import anyio.lowlevel
import pytest
from aiohttp import web

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._errors import ChannelError
from psi_agent.channel._types import FileChunk, ReasoningChunk, TextChunk


@pytest.mark.anyio
async def test_channel_core_connect_unix(tmp_path):
    """Core can connect to a Unix socket server."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=400)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        assert core._session is not None
        assert core._endpoint == "http://localhost/chat/completions"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_converts_file_chunk_to_recv_marker(tmp_path):
    """FileChunk becomes [RECV:path] in the POST body."""
    sock_path = str(tmp_path / "session.sock")
    received_body = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal received_body
        received_body = await request.json()
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        chunks = []
        async for chunk in core.post([FileChunk("/home/user/file.txt"), TextChunk("hello")]):
            chunks.append(chunk)

    expected_content = "[RECV:/home/user/file.txt]\nhello"
    assert received_body["messages"][0]["content"] == expected_content
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "ok"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_sse_buffering_merges_within_interval(tmp_path):
    """SSE chunks within interval are merged into one TextChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"hello "}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"world"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "hello world"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_sse_interval_split(tmp_path):
    """SSE chunks arriving after interval expiry yield separate TextChunks."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"first"}}]}\n\n')
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "first"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_detects_send_marker(tmp_path):
    """[SEND:/path] in SSE content yields FileChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        sse_line = (
            b'data: {"choices":[{"index":0,"delta":{"content":'
            b'"Here is [SEND:/tmp/output.py] the file. more text"}}]}\n\n'
        )
        await resp.write(sse_line)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert isinstance(chunks[0], FileChunk)
    assert chunks[0].path == "/tmp/output.py"
    assert isinstance(chunks[1], TextChunk)
    assert "Here is [SEND:/tmp/output.py] the file. more text" in chunks[1].text

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_send_dedup(tmp_path):
    """Same [SEND] path only yields FileChunk once."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        sse_line = b'data: {"choices":[{"index":0,"delta":{"content":"[SEND:/a.py] chunk1 [SEND:/a.py] chunk2"}}]}\n\n'
        await resp.write(sse_line)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        file_chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            if isinstance(chunk, FileChunk):
                file_chunks.append(chunk)

    assert len(file_chunks) == 1
    assert file_chunks[0].path == "/a.py"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_handles_error_chunk(tmp_path):
    """SSE chunk with finish_reason='error' raises."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        sse_line = (
            b'data: {"id":"error","choices":[{"index":0,'
            b'"delta":{"content":"[Upstream Error 401]: bad key"},'
            b'"finish_reason":"error"}]}\n\n'
        )
        await resp.write(sse_line)
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        with pytest.raises(ChannelError, match="Upstream Error 401"):
            async for _ in core.post([TextChunk("hi")]):
                pass

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_non_200_http_error(tmp_path):
    """Non-200 HTTP response raises with error message."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": {"message": "server error"}}, status=500)

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        with pytest.raises(ChannelError, match="server error"):
            async for _ in core.post([TextChunk("hi")]):
                pass

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_flush_on_stream_end(tmp_path):
    """Residual chunk_buf is flushed when stream ends."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"leftover"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "leftover"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_rejects_multiple_choices(tmp_path):
    """SSE chunk with >1 choices raises."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(
            b'data: {"choices":[{"index":0,"delta":{"content":"a"}},{"index":1,"delta":{"content":"b"}}]}\n\n'
        )
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path) as core:
        with pytest.raises(ChannelError, match="Expected exactly 1 choice"):
            async for _ in core.post([TextChunk("hi")]):
                pass

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_send_cross_chunk(tmp_path):
    """[SEND:...] split across SSE chunks is detected."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"here is [SEND:/tm"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"p/out.py] end"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        file_chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            if isinstance(chunk, FileChunk):
                file_chunks.append(chunk)

    assert len(file_chunks) == 1
    assert file_chunks[0].path == "/tmp/out.py"

    await runner.cleanup()


class _FakeSession:
    def __init__(self) -> None:
        self.close_called = False
        self.closed = False

    async def close(self) -> None:
        self.close_called = True
        await anyio.lowlevel.checkpoint()
        self.closed = True


@pytest.mark.anyio
async def test_aexit_closes_session_even_when_cancelled(monkeypatch):
    """__aexit__ must finish closing the session even while a cancel propagates."""
    core = ChannelCore(session_socket="/tmp/x.sock")
    fake = _FakeSession()
    monkeypatch.setattr(core, "_session", fake, raising=False)

    with anyio.CancelScope(shield=True):
        with anyio.CancelScope() as scope:
            scope.cancel()
            try:
                await core.__aexit__(None, None, None)
            except anyio.get_cancelled_exc_class():
                pass

    assert fake.close_called
    assert fake.closed


@pytest.mark.anyio
async def test_post_reasoning_only(tmp_path):
    """A reasoning-only delta yields a ReasoningChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"thinking"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "thinking"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_reasoning_then_content_ordered(tmp_path):
    """Type switch flushes reasoning before content, preserving order."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"think"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"answer"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "think"
    assert isinstance(chunks[1], TextChunk)
    assert chunks[1].text == "answer"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_reasoning_merges_within_interval(tmp_path):
    """Consecutive reasoning deltas within interval merge into one ReasoningChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"a"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"b"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=10.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], ReasoningChunk)
    assert chunks[0].text == "ab"

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_send_marker_ignored_in_reasoning(tmp_path):
    """[SEND:...] inside reasoning text does NOT yield a FileChunk."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"[SEND:/a.py] noted"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert not any(isinstance(c, FileChunk) for c in chunks)
    assert any(isinstance(c, ReasoningChunk) for c in chunks)

    await runner.cleanup()


@pytest.mark.anyio
async def test_post_null_delta_does_not_crash(tmp_path):
    """A chunk with delta=null must not crash post() (regression for delta.get on None)."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":null}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, sock_path)
    await site.start()
    await anyio.sleep(0.1)

    async with ChannelCore(sock_path, interval=0.0) as core:
        chunks = []
        async for chunk in core.post([TextChunk("hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert isinstance(chunks[0], TextChunk)
    assert chunks[0].text == "ok"

    await runner.cleanup()


class _RecordingResp:
    """Fake aiohttp response/context-manager that records when it is released."""

    def __init__(self, lines: list[bytes]) -> None:
        self.status = 200
        self.released = False
        self.content: AsyncIterator[bytes] = self._make_content(lines)

    @staticmethod
    async def _make_content(lines: list[bytes]) -> AsyncIterator[bytes]:
        for line in lines:
            yield line

    async def __aenter__(self) -> _RecordingResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.released = True

    async def text(self) -> str:
        return ""


class _RecordingPostSession:
    """Fake ClientSession whose post() returns a _RecordingResp."""

    def __init__(self, resp: _RecordingResp) -> None:
        self._resp = resp

    def post(self, endpoint: str, json: dict[str, object]) -> _RecordingResp:
        return self._resp

    async def close(self) -> None:
        pass


@pytest.mark.anyio
async def test_post_releases_response_on_early_break(monkeypatch):
    """Breaking out of post() (via aclosing) must release the upstream response.

    Clients consume ``core.post()`` under ``aclosing`` so an early break / cancel
    runs the generator's ``aclose()``, which unwinds the inner
    ``async with session.post(...) as resp`` and releases the streaming response.
    """
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"one"}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"two"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    core = ChannelCore(session_socket="/tmp/x.sock", interval=0.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    async with aclosing(core.post([TextChunk("hi")])) as gen:
        async for chunk in gen:
            assert isinstance(chunk, TextChunk)
            break

    assert resp.released is True
