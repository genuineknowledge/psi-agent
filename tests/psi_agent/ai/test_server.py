from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent._keys import API_KEY_KEY, BASE_URL_KEY, MODEL_KEY, PROVIDER_KEY
from psi_agent.ai.server import handle_chat_completions


class _FakeChunk:
    """Minimal stand-in for an any-llm ChatCompletionChunk."""

    def model_dump_json(self) -> str:
        return json.dumps({"id": "x", "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]})


class _TrackingStream:
    """Async iterator that records whether ``aclose()`` was awaited."""

    def __init__(self, chunks: list[Any], *, raise_after: int | None = None) -> None:
        self._chunks = list(chunks)
        self._i = 0
        self._raise_after = raise_after
        self.closed = False

    def __aiter__(self) -> _TrackingStream:
        return self

    async def __anext__(self) -> Any:
        if self._raise_after is not None and self._i >= self._raise_after:
            raise RuntimeError("upstream boom")
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk

    async def aclose(self) -> None:
        self.closed = True


async def _serve_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: _TrackingStream
) -> tuple[web.AppRunner, str]:
    async def fake_acompletion(**kwargs: Any) -> _TrackingStream:
        return stream

    monkeypatch.setattr("psi_agent.ai.server.acompletion", fake_acompletion)

    app = web.Application()
    app[PROVIDER_KEY] = "openai"
    app[MODEL_KEY] = "test"
    app[API_KEY_KEY] = "k"
    app[BASE_URL_KEY] = "http://upstream"
    app.router.add_post("/chat/completions", handle_chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "ai.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()
    await anyio.sleep(0.1)
    return runner, socket_path


async def _drain(socket_path: str) -> None:
    body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    connector = UnixConnector(path=socket_path)
    timeout = ClientTimeout(total=5)
    async with (
        ClientSession(connector=connector, timeout=timeout) as s,
        s.post("http://localhost/chat/completions", json=body) as resp,
    ):
        assert resp.status == 200
        async for _ in resp.content:
            pass


@pytest.mark.anyio
async def test_upstream_stream_closed_after_normal_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The upstream stream must be closed once the handler finishes streaming."""
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
        await anyio.sleep(0.05)
        assert stream.closed is True
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_upstream_stream_closed_after_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The upstream stream must be closed even when iteration raises mid-stream."""
    stream = _TrackingStream([_FakeChunk()], raise_after=1)
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
        await anyio.sleep(0.05)
        assert stream.closed is True
    finally:
        await runner.cleanup()
