from __future__ import annotations

import contextlib
import importlib
import json
import re
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web
from loguru import logger

from psi_agent.ai import server
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
    *,
    provider: str = "openai",
) -> tuple[web.AppRunner, str]:
    async def fake_acompletion(**kwargs: Any) -> _TrackingStream:
        if received_provider_kwargs is not None:
            received_provider_kwargs.update(kwargs)
        return stream

    monkeypatch.setattr("psi_agent.ai.server.acompletion", fake_acompletion)

    app = web.Application()
    app["provider"] = provider
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


# -- 兜底档位: 默认值可由环境变量改 (PSI_AI_REASONING_EFFORT) ------------------
#
# 变量名刻意抄成字面量而不是 import 常量: 它是**部署契约** (部署方在 compose /
# systemd 里写这个字符串), 改名必须让这里变红, 而不是跟着一起改。
_REASONING_EFFORT_ENV = "PSI_AI_REASONING_EFFORT"

_SetReasoningEffortEnv = Callable[[str | None], None]


@pytest.fixture
def reasoning_effort_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[_SetReasoningEffortEnv]:
    """设好 ``PSI_AI_REASONING_EFFORT`` 并重载 server 模块, 让模块常量跟着变。

    ``_DEFAULT_REASONING_EFFORT`` 是**模块常量, 进程启动时读一次**, 所以没有「每请求读
    环境变量」可以 patch —— 重新执行模块才等价于重启进程。``importlib.reload`` 在**同一个
    模块字典**里重跑模块代码, 因此本文件顶部 import 进来的 ``handle_chat_completions``
    也看得到新常量 (函数的 ``__globals__`` 就是那个字典)。

    退出时无条件按「未设置」再重载一次: 常量是模块态, 用例里留下的取值会污染同进程的
    后续用例, 而 monkeypatch 的 undo 管不到 import 期算出来的常量。
    """

    def _apply(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv(_REASONING_EFFORT_ENV, raising=False)
        else:
            monkeypatch.setenv(_REASONING_EFFORT_ENV, value)
        importlib.reload(server)

    yield _apply
    monkeypatch.delenv(_REASONING_EFFORT_ENV, raising=False)
    importlib.reload(server)


async def test_handler_requests_thinking_mode_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reasoning_effort_env: _SetReasoningEffortEnv
) -> None:
    """``deepseek`` provider 不传 ``reasoning_effort`` 时必须兜一个默认值, 否则思维模式被上游关掉。

    any-llm 的 DeepSeek provider 把缺省的 ``"auto"`` 读成「没要思维」, 转而下发
    ``extra_body.thinking={"type": "disabled"}``。模型被关掉思维通道后仍要推理,
    就把自我对话写进 ``content`` —— 即线上的 thinking 泄漏。

    不设 ``PSI_AI_REASONING_EFFORT`` 时兜的仍是 ``medium``: 那个变量只把「选哪一档」
    交出去, 一个字都不改默认行为。
    """
    reasoning_effort_env(None)
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    # 兜底只对会误关思维的 provider (deepseek) 生效
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received, provider="deepseek")
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert received["reasoning_effort"] == "medium"


async def test_reasoning_effort_follows_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reasoning_effort_env: _SetReasoningEffortEnv
) -> None:
    """部署方用环境变量选档, 不必改安装包里的常量。

    实测真链路同一批 6 题: ``medium`` 23.5s / ``minimal`` 13.1s / ``none`` 5.5s ——
    档位是延迟与可读性的权衡, 各部署取舍不同, 所以值归部署方。
    """
    reasoning_effort_env("minimal")
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received, provider="deepseek")
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert received["reasoning_effort"] == "minimal"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_blank_reasoning_effort_env_falls_back_to_medium(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reasoning_effort_env: _SetReasoningEffortEnv,
    blank: str,
) -> None:
    """空串/纯空白 = 没设置, 回落 ``medium``。

    「变量在、值没填」在部署里太容易发生 (compose 里留了个空的 ``PSI_AI_REASONING_EFFORT=``),
    而空串若原样下发, 进的是 any-llm ``CompletionParams`` 的**字面量**字段 —— 那不是
    「用默认档」, 是当场报错。
    """
    reasoning_effort_env(blank)
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received, provider="deepseek")
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert received["reasoning_effort"] == "medium", f"env={blank!r}"


async def test_handler_openai_provider_keeps_upstream_reasoning_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reasoning_effort_env: _SetReasoningEffortEnv
) -> None:
    """``openai`` provider 直连 DeepSeek 兼容端点时, 不传 ``reasoning_effort`` 不能兜默认值。

    openai provider 没有 any-llm deepseek provider 的 auto→disabled 逻辑: 不传时
    thinking 本来就开着, 思考照常进 ``reasoning_content``。若强制 ``"medium"``,
    模型反而把过程叙述写进 ``content`` —— 用户在飞书看到整段自我对话 (线上泄漏)。

    配了 ``PSI_AI_REASONING_EFFORT`` 也照样不补: 白名单判的是 provider 会不会误关
    思维, 与部署方有没有表态无关。
    """
    reasoning_effort_env("minimal")
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    # _serve_handler 默认 provider="openai"
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()

    assert "reasoning_effort" not in received


async def test_handler_keeps_caller_supplied_reasoning_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reasoning_effort_env: _SetReasoningEffortEnv
) -> None:
    """调用方显式给的值优先 —— 含 ``"none"``。

    这里是转发层: 兜底只为补上「谁都没表态」这一种情况, 不该覆盖上游意图。环境变量是
    **部署方**的表态, 比调用方低一级 —— 调用方 (Session) 为某一轮点了 ``"none"``,
    不能被它盖回去。
    """
    reasoning_effort_env("minimal")
    received: dict[str, Any] = {}
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream, received, provider="deepseek")
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


# -- turn markers ------------------------------------------------------------
#
# These two ends are the *authoritative* pair for model wall time (see the
# comment on ``_TURN_MARKER_OPEN`` in ai/server.py). Their counts must balance:
# a 10% shortfall in the agent-side markers previously under-reported model time
# by 24 percentage points (39.2% where 63.4% was correct), because the turns
# that fall off are exactly the slow ones on unusual branches.


def _capture_records() -> tuple[list[Any], int]:
    """Capture whole records at INFO — production's level, not DEBUG."""
    records: list[Any] = []
    sink_id = logger.add(lambda m: records.append(m.record), level="INFO")
    return records, sink_id


def _markers(records: list[Any]) -> tuple[list[str], list[str]]:
    messages = [r["message"] for r in records]
    return (
        [m for m in messages if m.startswith("ai-turn open")],
        [m for m in messages if m.startswith("ai-turn close")],
    )


@pytest.mark.anyio
async def test_turn_markers_balance_on_the_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V1: one open, one close, both at INFO, close carrying elapsed + outcome."""
    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    opens, closes = _markers(records)
    assert len(opens) == 1
    assert len(closes) == 1, "an unclosed turn is invisible to the probe scripts"
    assert "outcome=ok" in closes[0]
    assert re.search(r"elapsed_ms=\d+", closes[0]), closes[0]


@pytest.mark.anyio
async def test_turn_markers_balance_when_upstream_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V2: the error branch closes too, and says so in ``outcome``."""
    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()], raise_after=1)
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    opens, closes = _markers(records)
    assert len(opens) == len(closes) == 1
    assert "outcome=upstream_error" in closes[0]


@pytest.mark.anyio
async def test_turn_markers_balance_when_the_provider_call_itself_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V3: failing before a single chunk arrives still has to close the turn.

    This branch returns early, and used to return with no terminal log at all —
    one of the two sources of the count imbalance.
    """

    async def exploding_acompletion(**kwargs: Any) -> Any:
        raise RuntimeError("provider refused")

    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    monkeypatch.setattr("psi_agent.ai.server.acompletion", exploding_acompletion)
    try:
        await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    opens, closes = _markers(records)
    assert len(opens) == len(closes) == 1, f"opens={opens} closes={closes}"


@pytest.mark.anyio
async def test_turn_markers_balance_when_the_client_vanishes_before_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V3b: the ``response.prepare`` failure branch must close its turn too.

    This early return used to log only a warning and return with no terminal
    line at all — the second source of the open/close imbalance (the first being
    agent.py's markers never covering every path). Reached by making ``prepare``
    itself raise, which is what a client disconnecting mid-handshake does.
    """

    async def refuse_to_prepare(self: Any, request: Any) -> None:
        raise ConnectionResetError("client vanished")

    monkeypatch.setattr(web.StreamResponse, "prepare", refuse_to_prepare)

    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        with contextlib.suppress(Exception):
            await _drain(socket_path)
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    opens, closes = _markers(records)
    assert len(opens) == 1, f"opens={opens}"
    assert len(closes) == 1, f"the turn was opened and never closed: closes={closes}"
    assert "outcome=prepare_failed" in closes[0]


@pytest.mark.anyio
async def test_unparseable_body_is_not_counted_as_a_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V4: a request that never became a turn must not open one.

    It gets the third marker word instead, so it stays countable without
    unbalancing the open/close pair.
    """
    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=5)) as session,
            session.post(
                f"{socket_path}/chat/completions",
                data=b"{not json",
                headers={"Content-Type": "application/json"},
            ) as response,
        ):
            assert response.status == 400
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    opens, closes = _markers(records)
    assert opens == [] and closes == []
    rejected = [r["message"] for r in records if r["message"].startswith("ai-turn rejected")]
    assert len(rejected) == 1, "still has to leave a trace, just not a turn-shaped one"


@pytest.mark.anyio
async def test_session_id_from_routing_reaches_the_turn_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V5: ``routing.session_id`` is the only way this process learns whose turn it is.

    AI is a separate aiohttp app behind a socket, so the Session's ContextVar
    does not reach it. Without this the markers cannot be attributed to a person
    — which is the whole point of item 1.
    """
    records, sink_id = _capture_records()
    stream = _TrackingStream([_FakeChunk()])
    runner, socket_path = await _serve_handler(tmp_path, monkeypatch, stream)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=5)) as session,
            session.post(
                f"{socket_path}/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                    "routing": {"session_id": "feishu-ou_marker"},
                },
            ) as response,
        ):
            assert response.status == 200
            async for _ in response.content:
                pass
    finally:
        await runner.cleanup()
        logger.remove(sink_id)

    marked = [r for r in records if r["message"].startswith(("ai-turn open", "ai-turn close"))]
    assert marked, "no turn markers captured"
    for record in marked:
        assert record["extra"].get("psi_session") == "feishu-ou_marker", record["message"]


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
