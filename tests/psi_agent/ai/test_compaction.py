from __future__ import annotations

import contextlib
import json
import socket
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.ai.server import handle_chat_completions


class _CompactionFakeChunk:
    def __init__(
        self, content: str = "", finish_reason: str | None = None, usage: dict[str, int] | None = None
    ) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self.usage = _FakeUsage(**usage) if usage else None

    def model_dump_json(self) -> str:
        d: dict[str, Any] = {
            "id": "x",
            "choices": [{"index": 0, "delta": {"content": self._content}, "finish_reason": self._finish_reason}],
            "created": 0,
            "model": "test",
            "object": "chat.completion.chunk",
        }
        if self.usage:
            d["usage"] = self.usage.__dict__
        return json.dumps(d)


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _CompactionTrackingStream:
    def __init__(self, chunks: list[_CompactionFakeChunk]) -> None:
        self._chunks = list(chunks)
        self._i = 0
        self.closed = False

    def __aiter__(self) -> _CompactionTrackingStream:
        return self

    async def __anext__(self) -> _CompactionFakeChunk:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk

    async def aclose(self) -> None:
        self.closed = True


async def _serve_compaction_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: _CompactionTrackingStream,
    max_context_tokens: int = 0,
    received_kwargs: dict[str, Any] | None = None,
) -> tuple[web.AppRunner, str]:
    async def fake_acompletion(**kwargs: Any) -> _CompactionTrackingStream:
        if received_kwargs is not None:
            received_kwargs.update(kwargs)
        return stream

    monkeypatch.setattr("psi_agent.ai.server.acompletion", fake_acompletion)

    app = web.Application()
    app["provider"] = "openai"
    app["model"] = "test"
    app["api_key"] = "k"
    app["base_url"] = "http://upstream"
    app["max_context_tokens"] = max_context_tokens
    app.router.add_post("/chat/completions", handle_chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    site = web.SockSite(runner, sock)
    await site.start()
    await anyio.sleep(0.1)
    return runner, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def _read_sse(socket_path: str) -> list[dict[str, Any]]:
    body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    timeout = ClientTimeout(total=5)
    chunks: list[dict[str, Any]] = []
    async with (
        ClientSession(timeout=timeout) as s,
        s.post(f"{socket_path}/chat/completions", json=body) as resp,
    ):
        assert resp.status == 200
        async for raw_line in resp.content:
            line = raw_line.decode().strip()
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    chunks.append(json.loads(data_str))
    return chunks


@pytest.mark.anyio
async def test_compaction_signal_when_usage_exceeds_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _CompactionTrackingStream(
        [
            _CompactionFakeChunk(content="Hello", finish_reason="stop"),
            _CompactionFakeChunk(usage={"prompt_tokens": 50000, "completion_tokens": 200, "total_tokens": 50200}),
        ]
    )
    runner, socket_path = await _serve_compaction_handler(tmp_path, monkeypatch, stream, max_context_tokens=10000)
    try:
        chunks = await _read_sse(socket_path)
        assert len(chunks) >= 2
        usage_chunk = next(chunk for chunk in chunks if "psi_usage" in chunk)
        assert usage_chunk["choices"][0]["finish_reason"] == "usage"
        assert usage_chunk["psi_usage"] == {
            "prompt_tokens": 50000,
            "completion_tokens": 200,
            "total_tokens": 50200,
        }
        compaction_chunk = chunks[-1]
        assert "psi_compaction" in compaction_chunk
        assert compaction_chunk["psi_compaction"]["needed"] is True
        assert compaction_chunk["psi_compaction"]["prompt_tokens"] == 50000
        assert compaction_chunk["psi_compaction"]["threshold"] == 10000
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_no_compaction_signal_when_under_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _CompactionTrackingStream(
        [
            _CompactionFakeChunk(content="Hello", finish_reason="stop"),
            _CompactionFakeChunk(usage={"prompt_tokens": 5000, "completion_tokens": 200, "total_tokens": 5200}),
        ]
    )
    runner, socket_path = await _serve_compaction_handler(tmp_path, monkeypatch, stream, max_context_tokens=10000)
    try:
        chunks = await _read_sse(socket_path)
        usage_chunk = next(chunk for chunk in chunks if "psi_usage" in chunk)
        assert usage_chunk["psi_usage"]["total_tokens"] == 5200
        for chunk in chunks:
            assert "psi_compaction" not in chunk
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_no_compaction_when_max_context_tokens_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _CompactionTrackingStream(
        [
            _CompactionFakeChunk(content="Hello", finish_reason="stop"),
            _CompactionFakeChunk(usage={"prompt_tokens": 50000, "completion_tokens": 200, "total_tokens": 50200}),
        ]
    )
    runner, socket_path = await _serve_compaction_handler(tmp_path, monkeypatch, stream, max_context_tokens=0)
    try:
        chunks = await _read_sse(socket_path)
        assert any("psi_usage" in chunk for chunk in chunks)
        for chunk in chunks:
            assert "psi_compaction" not in chunk
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_stream_options_include_usage_forced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    received_kwargs: dict[str, Any] = {}
    stream = _CompactionTrackingStream(
        [
            _CompactionFakeChunk(content="Hello", finish_reason="stop"),
        ]
    )
    runner, socket_path = await _serve_compaction_handler(
        tmp_path, monkeypatch, stream, max_context_tokens=10000, received_kwargs=received_kwargs
    )
    try:
        await _read_sse(socket_path)
    finally:
        await runner.cleanup()

    assert received_kwargs["stream_options"] == {"include_usage": True}
