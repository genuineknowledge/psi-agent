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
async def test_post_reasoning_kind_switch_emits_separate_chunks(tmp_path):
    """Different delta.kind must not merge even inside a long interval window."""
    sock_path = str(tmp_path / "session.sock")

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"think","kind":"thinking"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"index":0,"delta":{"reasoning":"call","kind":"tool_call"}}]}\n\n')
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
    assert isinstance(chunks[1], ReasoningChunk)
    assert chunks[0].text == "think"
    assert chunks[0].kind == "thinking"
    assert chunks[1].text == "call"
    assert chunks[1].kind == "tool_call"

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


# -- 出向文件的来源地址 ---------------------------------------------------------
#
# 跨容器时 [SEND:] 里的路径在 channel 这一侧读不到 (Session 在别的容器, 各挂自己的卷),
# 所以 FileChunk 要带上「字节从哪儿取」。本地 Session 必须留空, 否则会为已经能直接读的
# 文件多绕一趟 HTTP。


def test_byte_source_filled_for_tcp_session():
    """TCP 地址 = Session 在别的容器, 要填 (末尾斜杠归一, 免得拼出 //files)。"""
    assert ChannelCore("http://psi-agent-luolin:8081")._byte_source == "http://psi-agent-luolin:8081"
    assert ChannelCore("https://host:8443/")._byte_source == "https://host:8443"


def test_byte_source_empty_for_local_session():
    """Unix socket / 命名管道 = 同机同文件系统, 路径本就可读, 留空。"""
    assert ChannelCore("/tmp/psi/channels/x.sock")._byte_source == ""
    assert ChannelCore(r"\.\pipe\psi\channels\x")._byte_source == ""


@pytest.mark.anyio
async def test_post_stamps_source_on_send_marker_for_tcp(monkeypatch):
    """扫出的 FileChunk 必须带上来源地址 —— 少这一步, 下游拿不到字节就退回读本地路径。"""
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"see [SEND:/workspace/x.md] done"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    core = ChannelCore(session_socket="http://psi-agent-chengxx:8081", interval=0.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://psi-agent-chengxx:8081/chat/completions", raising=False)

    files = [c async for c in core.post([TextChunk("hi")]) if isinstance(c, FileChunk)]

    assert len(files) == 1
    assert files[0].path == "/workspace/x.md"
    assert files[0].source == "http://psi-agent-chengxx:8081"


@pytest.mark.anyio
async def test_post_leaves_source_empty_for_local(monkeypatch):
    """本地 Session 的 FileChunk 不带地址 —— 与升级前零行为差异。"""
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"see [SEND:/tmp/x.md] done"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    core = ChannelCore(session_socket="/tmp/x.sock", interval=0.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    files = [c async for c in core.post([TextChunk("hi")]) if isinstance(c, FileChunk)]

    assert len(files) == 1
    assert files[0].source == ""


class _StallingResp(_RecordingResp):
    """Fake response that pauses ``stall`` seconds before its final lines.

    Reproduces the observed upstream shape: content arrives, the model then goes
    quiet well past the buffer interval, and only afterwards does ``[DONE]`` come.
    """

    def __init__(self, head: list[bytes], stall: float, tail: list[bytes]) -> None:
        super().__init__([])
        self.content: AsyncIterator[bytes] = self._stalling(head, stall, tail)

    @staticmethod
    async def _stalling(head: list[bytes], stall: float, tail: list[bytes]) -> AsyncIterator[bytes]:
        for line in head:
            yield line
        await anyio.sleep(stall)
        for line in tail:
            yield line


@pytest.mark.anyio
async def test_post_drains_tail_while_upstream_is_silent(monkeypatch):
    """A tail buffered before a long upstream pause reaches the user during the pause.

    Regression: the interval window is lazy (checked only on the next delta), so a
    quiet upstream left the last chars invisible until ``[DONE]`` — the reply looked
    cut off mid-sentence. Asserting on *arrival time*, not just final content: the
    old code produced the same text, only too late.
    """
    resp = _StallingResp(
        [b'data: {"choices":[{"index":0,"delta":{"content":"tail text"}}]}\n\n'],
        stall=1.0,
        tail=[b"data: [DONE]\n\n"],
    )
    # interval high enough that only the idle drain can emit this tail.
    core = ChannelCore(session_socket="/tmp/x.sock", interval=10.0, idle_drain=0.2)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    start = anyio.current_time()
    seen: list[tuple[float, str]] = []
    async for chunk in core.post([TextChunk("hi")]):
        if isinstance(chunk, TextChunk):
            seen.append((anyio.current_time() - start, chunk.text))

    assert [text for _, text in seen] == ["tail text"]
    # Arrived during the 1s silence, not at [DONE].
    assert seen[0][0] < 0.9


@pytest.mark.anyio
async def test_post_idle_drain_does_not_split_active_stream(monkeypatch):
    """Deltas arriving steadily still coalesce — the drain only fires on real silence."""
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"b"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    core = ChannelCore(session_socket="/tmp/x.sock", interval=10.0, idle_drain=5.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    texts = [c.text async for c in core.post([TextChunk("hi")]) if isinstance(c, TextChunk)]

    assert texts == ["ab"]


@pytest.mark.anyio
async def test_post_idle_drain_disabled_keeps_legacy_path(monkeypatch):
    """``idle_drain<=0`` skips the pump task entirely — unchanged behaviour."""
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    core = ChannelCore(session_socket="/tmp/x.sock", interval=10.0, idle_drain=0.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    texts = [c.text async for c in core.post([TextChunk("hi")]) if isinstance(c, TextChunk)]

    assert texts == ["x"]


@pytest.mark.anyio
async def test_post_surfaces_bare_channel_error_with_idle_drain_on(monkeypatch):
    """Parser errors must stay a bare ChannelError when idle drain is enabled.

    Callers and the rest of this file catch ``ChannelError`` directly, so anything
    that wrapped it (an ``ExceptionGroup`` from a task group, say) would slip past
    every ``except ChannelError`` in the codebase.
    """
    resp = _RecordingResp(
        [
            b'data: {"choices":[{"delta":{"content":"[Upstream Error]: boom"},"finish_reason":"error"}]}\n\n',
        ]
    )
    # interval>0 is required for the idle timeout to be armed at all.
    core = ChannelCore(session_socket="/tmp/x.sock", interval=10.0, idle_drain=5.0)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    with pytest.raises(ChannelError, match="Upstream Error"):
        async for _ in core.post([TextChunk("hi")]):
            pass


@pytest.mark.anyio
async def test_post_early_break_releases_response_with_idle_drain(monkeypatch):
    """Early break must unwind cleanly while the idle timeout is armed.

    Regression for a real defect in the first cut of this feature: the idle timeout
    was implemented with a pump task, and yielding out of a generator that owns a
    task group blows up on early close — the cancel scope gets exited from a
    different task and anyio raises ``RuntimeError: Attempted to exit cancel scope
    in a different task``, leaving the upstream generator unfinalized. The timeout
    must therefore sit on the raw byte read with no ``yield`` inside its scope.
    """
    resp = _StallingResp(
        [
            b'data: {"choices":[{"index":0,"delta":{"content":"first"}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"second"}}]}\n\n',
        ],
        stall=30.0,
        tail=[b"data: [DONE]\n\n"],
    )
    # A tiny interval still emits per delta (so there is something to break on) while
    # keeping interval>0, which is what arms the idle timeout.
    core = ChannelCore(session_socket="/tmp/x.sock", interval=0.01, idle_drain=0.2)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    async with aclosing(core.post([TextChunk("hi")])) as gen:
        async for chunk in gen:
            if isinstance(chunk, TextChunk):
                break

    assert resp.released


@pytest.mark.anyio
async def test_post_early_break_after_idle_tick_unwinds_cleanly(monkeypatch):
    """Breaking *after* an idle tick has fired must not raise from the cancel scope.

    Tightest form of the regression above: the break has to happen once at least one
    ``move_on_after`` scope has already been entered and left, which is exactly the
    state that made the task-group version raise during ``aclose()``.
    """
    resp = _StallingResp(
        [b'data: {"choices":[{"index":0,"delta":{"content":"head"}}]}\n\n'],
        stall=30.0,
        tail=[b"data: [DONE]\n\n"],
    )
    core = ChannelCore(session_socket="/tmp/x.sock", interval=10.0, idle_drain=0.2)
    monkeypatch.setattr(core, "_session", _RecordingPostSession(resp), raising=False)
    monkeypatch.setattr(core, "_endpoint", "http://localhost/chat/completions", raising=False)

    # The only TextChunk that can arrive here is the idle drain of "head".
    async with aclosing(core.post([TextChunk("hi")])) as gen:
        async for chunk in gen:
            if isinstance(chunk, TextChunk):
                assert chunk.text == "head"
                break

    assert resp.released
