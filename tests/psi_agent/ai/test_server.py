from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web
from loguru import logger

from psi_agent.ai.server import _describe_delta, _describe_messages, handle_chat_completions


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: _TrackingStream,
    received_provider_kwargs: dict[str, Any] | None = None,
) -> tuple[web.AppRunner, str]:
    async def fake_acompletion(**kwargs: Any) -> _TrackingStream:
        if received_provider_kwargs is not None:
            received_provider_kwargs.update(kwargs)
        return stream

    monkeypatch.setattr("psi_agent.ai.server.acompletion", fake_acompletion)

    app = web.Application()
    app["provider"] = "openai"
    app["model"] = "test"
    app["api_key"] = "k"
    app["base_url"] = "http://upstream"
    app.router.add_post("/chat/completions", handle_chat_completions)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    site = web.SockSite(runner, sock)
    await site.start()
    await anyio.sleep(0.1)
    return runner, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def _drain(socket_path: str) -> None:
    body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    timeout = ClientTimeout(total=5)
    async with (
        ClientSession(timeout=timeout) as s,
        s.post(f"{socket_path}/chat/completions", json=body) as resp,
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


@pytest.mark.anyio
async def test_handler_strips_internal_routing_before_calling_the_external_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider-facing calls must never receive the Router's Session metadata."""

    received_provider_kwargs: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received_provider_kwargs)
    try:
        async with (
            ClientSession() as session,
            session.post(
                f"{socket_path}/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                    "routing": {"session_id": "private-session"},
                    "temperature": 0.2,
                },
            ) as response,
        ):
            assert response.status == 200
            async for _ in response.content:
                pass
    finally:
        await runner.cleanup()

    assert "routing" not in received_provider_kwargs
    assert received_provider_kwargs["temperature"] == 0.2


async def test_handler_passes_http_client_through_client_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """自带的 httpx client 必须经 ``client_args`` 进 provider 的 client 构造。

    放进请求体 (**body) 是不行的 —— 那是发给上游的 JSON 字段。这条线断了的表现是
    对话请求全部超时, 见 psi_agent._tls。
    """
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    sentinel = object()
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received)
    runner.app["http_client"] = sentinel
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert received["client_args"] == {"http_client": sentinel}


async def test_handler_omits_client_args_without_http_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 client 时不能传空的 ``client_args``: 那些 provider 走 any-llm 默认。"""
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert "client_args" not in received


async def test_handler_requests_thinking_mode_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 ``reasoning_effort`` 时必须兜一个默认值, 否则思维模式被上游关掉。

    any-llm 的 DeepSeek provider 把缺省的 ``"auto"`` 读成「没要思维」, 转而下发
    ``extra_body.thinking={"type": "disabled"}``。模型被关掉思维通道后仍要推理,
    就把自我对话写进 ``content`` —— 即线上的 thinking 泄漏。
    """
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert received["reasoning_effort"] == "medium"


async def test_handler_keeps_caller_supplied_reasoning_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """调用方显式给的值优先 —— 含 ``"none"``。

    这里是转发层: 兜底只为补上「谁都没表态」这一种情况, 不该覆盖上游意图。
    """
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received)
    try:
        async with (
            ClientSession() as session,
            session.post(
                f"{socket_path}/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                    "reasoning_effort": "none",
                },
            ) as response,
        ):
            assert response.status == 200
            async for _ in response.content:
                pass
    finally:
        await runner.cleanup()

    assert received["reasoning_effort"] == "none"


def _delta_payload(delta: dict[str, Any]) -> str:
    return json.dumps({"id": "x", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})


def test_census_distinguishes_content_only_from_reasoning_content() -> None:
    """V6: the census line must separate hypothesis (a) from (b).

    (a) the model never used a reasoning channel — self-talk came straight out
    of ``content``; (b) it emitted ``reasoning_content`` and ``ai_client.py``,
    which reads ``reasoning`` only, dropped it. Both look identical in the
    stored history, which is why the raw stream needs its own judgement.
    """
    only_content = _describe_delta(_delta_payload({"content": "wait, let me think about this", "role": "assistant"}))
    assert "content=29ch" in only_content
    assert "reasoning=ABSENT" in only_content
    assert "reasoning_content=ABSENT" in only_content
    assert "thinking=ABSENT" in only_content

    only_reasoning_content = _describe_delta(_delta_payload({"reasoning_content": "hmm, the user wants"}))
    assert "reasoning_content=19ch" in only_reasoning_content
    assert "reasoning=ABSENT" in only_reasoning_content
    assert "content=ABSENT" in only_reasoning_content


def test_census_is_not_truncated_by_a_long_content() -> None:
    """The whole point: a huge ``content`` must not hide the reasoning keys.

    The raw-chunk line truncates; this one is bounded by field *count*, so a key
    that was never sent stays distinguishable from one pushed past a cutoff.
    """
    payload = _delta_payload({"content": "x" * 50_000, "reasoning_content": "the-tell"})
    census = _describe_delta(payload)

    assert len(census) < 200
    assert "content=50000ch" in census
    assert "reasoning_content=8ch" in census


def test_census_never_echoes_message_text() -> None:
    """Only names, lengths and presence — the census must not repeat content."""
    census = _describe_delta(_delta_payload({"content": "sensitive-user-secret"}))
    assert "sensitive-user-secret" not in census


def test_census_reports_unknown_delta_fields() -> None:
    """An unrecognised reasoning-ish key must not vanish silently."""
    census = _describe_delta(_delta_payload({"content": "hi", "reasoning_detail": "x"}))
    assert "other=['reasoning_detail']" in census


def test_census_counts_tool_calls() -> None:
    census = _describe_delta(_delta_payload({"tool_calls": [{"id": "a"}, {"id": "b"}]}))
    assert "tool_calls=2" in census


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("not json at all", "unparseable"),
        ("[1, 2]", "non-object payload (list)"),
        ('{"choices": []}', "no choices"),
        ('{"choices": [1]}', "non-object choice (int)"),
        ('{"choices": [{"delta": null}]}', "no delta (NoneType)"),
    ],
)
def test_census_survives_malformed_payloads(payload: str, expected: str) -> None:
    """A logging helper must never be the thing that breaks the stream."""
    assert _describe_delta(payload) == expected


def test_message_census_distinguishes_reasoning_on_the_wire() -> None:
    """The request-side judgement: was ``reasoning_content`` sent, or not?

    Production measured every response chunk with reasoning ABSENT. That rules
    out "the model sent it and we dropped it", but not why the channel stayed
    shut. This line answers the half we could not see: what *we* sent.
    """
    without = _describe_messages([{"role": "user", "content": "继续啊"}])
    assert "reasoning_carriers=0" in without
    assert "reasoning_content" not in without

    with_reasoning = _describe_messages(
        [
            {"role": "user", "content": "继续啊"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "hmm, the user wants",
                "tool_calls": [{"id": "a"}],
            },
        ]
    )
    assert "reasoning_carriers=1" in with_reasoning
    assert "1assistant(reasoning_content=19ch, tool_calls=1, content=0ch)" in with_reasoning


def test_message_census_is_not_truncated_by_a_long_history() -> None:
    """Bounded by message *count*, not payload size — the whole point.

    ``Request body`` truncates, so a ``reasoning_content`` sitting past the
    cutoff is indistinguishable from one never sent. This line must stay short
    no matter how large the history is.
    """
    history = [{"role": "user", "content": "x" * 50_000} for _ in range(20)]
    history.append({"role": "assistant", "content": "y" * 90_000, "reasoning_content": "z" * 40_000})
    census = _describe_messages(history)

    assert "reasoning_carriers=1" in census
    assert "reasoning_content=40000ch" in census
    assert len(census) < 900, f"census grew with payload size: {len(census)} chars"


def test_message_census_never_echoes_message_text() -> None:
    """Logs already carry real conversations; the census must not add more."""
    census = _describe_messages(
        [{"role": "user", "content": "sensitive-user-secret", "reasoning_content": "private-thought"}]
    )
    assert "sensitive-user-secret" not in census
    assert "private-thought" not in census
    assert "content=21ch" in census


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        ("not a list", "non-list messages (str)"),
        ([], "no messages"),
        (None, "non-list messages (NoneType)"),
    ],
)
def test_message_census_survives_malformed_input(messages: Any, expected: str) -> None:
    """Same rule as the delta census: never break the request over a log line."""
    assert _describe_messages(messages) == expected


def test_message_census_tolerates_non_dict_and_odd_content() -> None:
    """Multimodal ``content`` is a list, and a malformed entry must not raise."""
    census = _describe_messages(["bare string", {"role": "user", "content": [{"type": "text"}]}])
    assert "0non-object(str)" in census
    assert "content=list" in census


@pytest.mark.anyio
async def test_census_is_logged_for_each_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The census must actually reach the log, once per chunk."""
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    census_lines = [m for m in messages if m.startswith("delta keys:")]
    assert len(census_lines) == 1
    assert "content=2ch" in census_lines[0]
    assert "reasoning=ABSENT" in census_lines[0]


@pytest.mark.anyio
async def test_message_census_is_logged_once_per_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The request census must reach the log too — once, describing what we sent."""
    logged: list[str] = []
    sink_id = logger.add(lambda m: logged.append(m.record["message"]), level="DEBUG")
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    census_lines = [m for m in logged if m.startswith("message census:")]
    assert len(census_lines) == 1
    # ``_drain`` sends a single user message with no reasoning field.
    assert "n=1 reasoning_carriers=0" in census_lines[0]
    assert "0user(content=2ch)" in census_lines[0]
