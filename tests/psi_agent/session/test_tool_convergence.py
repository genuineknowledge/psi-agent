"""Round convergence: a refused tool call must *say* it was refused.

`feishu_docs_search` was called 305 times with reworded keywords until the
upstream returned HTTP 402.  The loop closes only if the model can tell three
situations apart -- there is nothing, the result was cut, this search is
exhausted -- so every criterion here checks the **text the model receives**, not
merely that the tool stopped running.  A silent stop would satisfy "the tool was
not called" and still leave the incident intact.

The end-to-end cases therefore assert against the request bodies the agent
actually sends upstream, captured from the mock provider, rather than against
the chunk stream (which is what the *user* sees) or the tracker's internals.
"""

from __future__ import annotations

import json
import socket as _s
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.tool_convergence import (
    CALL_SURFACE_ERROR_LIMIT,
    CALL_SURFACE_NOTICE,
    REPEAT_LIMIT,
    UNPRODUCTIVE_LIMIT,
    UNPRODUCTIVE_NOTICE,
    ToolCallConvergence,
    is_call_surface_error,
    is_refusal_notice,
    is_unproductive_result,
)
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    "result",
    [
        "",
        "   \n  ",
        "Error executing tool 'x': boom",
        "未找到匹配的文档",
        "No results found",
        "[]",
        '{"items": []}',
        '{"ok": true, "total": 0}',
        '{"data": {}}',
    ],
    ids=[
        "empty",
        "whitespace",
        "error",
        "chinese-marker",
        "english-marker",
        "empty-array",
        "empty-items",
        "zero-total",
        "empty-envelope",
    ],
)
def test_unproductive_results_are_recognized(result: str) -> None:
    assert is_unproductive_result(result) is True


@pytest.mark.parametrize(
    "result",
    [
        '{"items": [{"title": "spec"}]}',
        '{"ok": true, "total": 3}',
        "找到 2 篇文档: A, B",
        "Weather in Beijing: sunny",
    ],
    ids=["items", "total", "chinese-hit", "prose"],
)
def test_productive_results_are_not_flagged(result: str) -> None:
    assert is_unproductive_result(result) is False


def test_calls_pass_through_below_the_futility_limit() -> None:
    conv = ToolCallConvergence()
    for i in range(UNPRODUCTIVE_LIMIT - 1):
        assert conv.refusal_for("search", {"q": f"w{i}"}) is None
        conv.record("search", {"q": f"w{i}"}, "[]")
    assert conv.refusal_for("search", {"q": "another"}) is None


def test_rewording_does_not_escape_the_futility_limit() -> None:
    """The 305-call shape: every attempt has different arguments."""
    conv = ToolCallConvergence()
    for i in range(UNPRODUCTIVE_LIMIT):
        assert conv.refusal_for("search", {"q": f"word{i}"}) is None
        conv.record("search", {"q": f"word{i}"}, "未找到")
    refusal = conv.refusal_for("search", {"q": "yet another wording"})
    assert refusal is not None
    # The notice has to carry all three facts, or the model cannot distinguish
    # it from one more empty hit.
    assert "未执行" in refusal
    assert "search" in refusal
    assert str(UNPRODUCTIVE_LIMIT) in refusal


def test_futility_is_tracked_per_tool() -> None:
    conv = ToolCallConvergence()
    for i in range(UNPRODUCTIVE_LIMIT + 2):
        conv.record("search", {"q": f"w{i}"}, "[]")
    assert conv.refusal_for("search", {"q": "x"}) is not None
    # A different tool is unaffected: the limit is evidence about one tool.
    assert conv.refusal_for("read_file", {"path": "a"}) is None


def test_a_productive_result_clears_the_futility_streak() -> None:
    conv = ToolCallConvergence()
    for i in range(UNPRODUCTIVE_LIMIT - 1):
        conv.record("search", {"q": f"w{i}"}, "[]")
    conv.record("search", {"q": "good"}, '{"items": [{"id": 1}]}')
    # Streak reset, so the next futile run must start over rather than trip
    # immediately on the leftover count.
    conv.record("search", {"q": "w-after"}, "[]")
    assert conv.refusal_for("search", {"q": "next"}) is None


def test_identical_arguments_stop_repeating_even_when_productive() -> None:
    conv = ToolCallConvergence()
    payload = '{"items": [{"id": 1}]}'
    for _ in range(REPEAT_LIMIT):
        assert conv.refusal_for("read_doc", {"id": "abc"}) is None
        conv.record("read_doc", {"id": "abc"}, payload)
    refusal = conv.refusal_for("read_doc", {"id": "abc"})
    assert refusal is not None
    assert "未执行" in refusal
    assert "read_doc" in refusal
    # Different arguments are still allowed: the repeat limit is about identity.
    assert conv.refusal_for("read_doc", {"id": "def"}) is None


def test_argument_key_ignores_json_key_order() -> None:
    conv = ToolCallConvergence()
    for _ in range(REPEAT_LIMIT):
        conv.record("search", {"q": "a", "page": 1}, "hit")
    # Same call, keys emitted in the other order -- must count as the same.
    assert conv.refusal_for("search", {"page": 1, "q": "a"}) is not None


def test_a_refusal_does_not_feed_the_counters() -> None:
    """Refusals must not be recorded, or one threshold becomes a permanent ban."""
    conv = ToolCallConvergence()
    for i in range(UNPRODUCTIVE_LIMIT):
        conv.record("search", {"q": f"w{i}"}, "[]")
    assert conv.refusal_for("search", {"q": "x"}) is not None
    # ``refusal_for`` is a query, not a write: asking twice reports the same
    # count rather than an inflated one.
    second = conv.refusal_for("search", {"q": "y"})
    assert second is not None
    assert str(UNPRODUCTIVE_LIMIT) in second
    conv.record("search", {"q": "real"}, '{"items": [1]}')
    assert conv.refusal_for("search", {"q": "z"}) is None


def test_recording_a_refusal_notice_is_a_no_op() -> None:
    """Feeding a notice back must not move any counter.

    Written after mutation review: a mutant that recorded refusals alongside
    executed calls survived every other criterion here, because the notice text
    happens to contain "没有查到" and so classified as unproductive -- the counter
    kept climbing and the tool stayed refused, which looked correct by accident.
    The guarantee is that refusals carry no evidence, so it is asserted directly.
    """
    conv = ToolCallConvergence()
    notice = UNPRODUCTIVE_NOTICE.format(name="search", count=UNPRODUCTIVE_LIMIT)
    for _ in range(UNPRODUCTIVE_LIMIT + REPEAT_LIMIT + 5):
        conv.record("search", {"q": "same"}, notice)
    assert conv.refusal_for("search", {"q": "same"}) is None
    assert conv.refusal_for("search", {"q": "other"}) is None
    assert is_refusal_notice(notice) is True
    assert is_refusal_notice('{"items": []}') is False


def test_empty_tool_name_does_not_trip_per_tool_gates() -> None:
    """Empty name alone must not feed futility/repeat maps."""
    conv = ToolCallConvergence()
    for _ in range(UNPRODUCTIVE_LIMIT + REPEAT_LIMIT):
        conv.record("", {}, "[]")
    assert conv.refusal_for("", {}) is None


@pytest.mark.parametrize(
    "result",
    [
        "Error: Tool 'feishu_user_get' not found",
        "Error: empty tool call name",
        "Error: Tool 'x' arguments must be a JSON object",
        "Error: Tool 'x' arguments must be valid JSON",
        "Error executing tool 'feishu_api': got an unexpected keyword argument 'uri_path'",
        "Error executing tool 'feishu_api': missing 1 required positional argument: 'uri'",
    ],
    ids=[
        "not-found",
        "empty-name",
        "args-not-object",
        "args-bad-json",
        "unexpected-keyword",
        "missing-required",
    ],
)
def test_call_surface_errors_are_recognized(result: str) -> None:
    assert is_call_surface_error(result) is True


def test_ordinary_tool_errors_are_not_call_surface() -> None:
    assert is_call_surface_error("Error executing tool 'x': boom") is False
    assert is_call_surface_error('{"items": []}') is False


def test_inventing_different_wrong_tool_names_trips_call_surface_gate() -> None:
    """The feishu_* invention shape: each wrong name would reset a per-tool streak."""
    conv = ToolCallConvergence()
    for i in range(CALL_SURFACE_ERROR_LIMIT):
        name = f"feishu_ghost_{i}"
        assert conv.refusal_for(name, {"q": "x"}) is None
        conv.record(name, {"q": "x"}, f"Error: Tool '{name}' not found")
    refusal = conv.refusal_for("feishu_ghost_next", {"q": "x"})
    assert refusal is not None
    assert "未执行" in refusal
    assert "工具名不存在或参数非法" in refusal
    assert str(CALL_SURFACE_ERROR_LIMIT) in refusal
    # Even a blank name is refused once the turn-level gate is open.
    assert conv.refusal_for("", {}) is not None


def test_invalid_args_also_feed_the_call_surface_gate() -> None:
    conv = ToolCallConvergence()
    conv.record("feishu_api", {}, "Error: Tool 'feishu_api' arguments must be valid JSON")
    conv.record(
        "feishu_api",
        {"uri_path": "/x"},
        "Error executing tool 'feishu_api': got an unexpected keyword argument 'uri_path'",
    )
    refusal = conv.refusal_for("feishu_api", {"uri": "/x"})
    assert refusal is not None
    assert is_refusal_notice(refusal)
    assert "工具名不存在或参数非法" in refusal
    assert str(CALL_SURFACE_ERROR_LIMIT) in refusal


def test_recording_a_call_surface_refusal_is_a_no_op() -> None:
    conv = ToolCallConvergence()
    notice = CALL_SURFACE_NOTICE.format(count=CALL_SURFACE_ERROR_LIMIT)
    for _ in range(5):
        conv.record("feishu_ghost", {}, notice)
    assert conv.refusal_for("anything", {}) is None


# --- end to end: what the model actually receives ------------------------------


def _tool_call_chunk(name: str, arguments: str) -> dict[str, Any]:
    return {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def _stop_chunk(content: str) -> dict[str, Any]:
    return {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
    }


class _RewordingProvider:
    """Mock upstream that keeps re-asking for the same tool with new wording.

    Reproduces the incident's shape rather than a fixed script: it emits a tool
    call for every round until ``rounds`` is exhausted, so the number of calls
    the agent actually dispatches is decided by the agent, not by the fixture.
    """

    def __init__(self, tool: str, rounds: int) -> None:
        self._tool = tool
        self._rounds = rounds
        self.requests: list[dict[str, Any]] = []

    async def handler(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(await request.json())
        turn = len(self.requests)
        chunk = (
            _tool_call_chunk(self._tool, json.dumps({"query": f"wording-{turn}"}))
            if turn <= self._rounds
            else _stop_chunk("查不到, 先停下来")
        )
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp


async def _run_against(provider: _RewordingProvider, tool_name: str, func) -> list[dict[str, Any]]:
    """Drive a real ``SessionAgent`` turn against ``provider``; return its requests."""
    app = web.Application()
    app.router.add_post("/chat/completions", provider.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        tf = ToolFunction(
            name=tool_name,
            description="Search docs.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(
                files={"__test__": FileEntry(file_hash="", tools={tool_name: tf}, funcs={tool_name: func})}
            ),
        )
        async for _ in agent.run({"role": "user", "content": "找一下设计文档"}):
            pass
        return provider.requests
    finally:
        await runner.cleanup()


def _tool_contents(request: dict[str, Any]) -> list[str]:
    return [m.get("content", "") for m in request.get("messages", []) if m.get("role") == "tool"]


@pytest.mark.anyio
async def test_model_is_told_the_search_was_refused_not_that_it_was_empty(tmp_path: Path) -> None:
    """The load-bearing case: refusal reaches the model as an explicit statement."""
    calls: list[dict[str, Any]] = []

    async def docs_search(query: str) -> str:
        calls.append({"query": query})
        return '{"items": []}'

    provider = _RewordingProvider("feishu_docs_search", rounds=UNPRODUCTIVE_LIMIT + 3)
    requests = await _run_against(provider, "feishu_docs_search", docs_search)

    # The tool stopped being executed ...
    assert len(calls) == UNPRODUCTIVE_LIMIT, f"tool ran {len(calls)} times, expected {UNPRODUCTIVE_LIMIT}"
    # ... and the model was told so, in the content of a tool result.
    notices = [c for req in requests for c in _tool_contents(req) if "未执行" in c]
    assert notices, "no refusal notice reached the model"
    notice = notices[0]
    assert "feishu_docs_search" in notice
    # Naming the wrong inference is the point of the notice: without this
    # sentence the model reads a refusal as one more empty result and rewords.
    assert "换个关键词再试同一个工具不会有新结果" in notice
    # And the notice must not look like an empty result to a length check.
    assert len(notice) > 60


@pytest.mark.anyio
async def test_normal_searches_carry_no_refusal_notice(tmp_path: Path) -> None:
    """No refusal, no notice -- the reverse case that keeps this from passing vacuously."""
    calls: list[str] = []

    async def docs_search(query: str) -> str:
        calls.append(query)
        return '{"items": [{"title": "spec", "url": "u"}]}'

    rounds = UNPRODUCTIVE_LIMIT + 3
    provider = _RewordingProvider("feishu_docs_search", rounds=rounds)
    requests = await _run_against(provider, "feishu_docs_search", docs_search)

    # Productive results never trip the futility limit, and each call carries
    # different arguments, so nothing is refused.
    assert len(calls) == rounds
    for req in requests:
        for content in _tool_contents(req):
            assert "未执行" not in content
            assert is_unproductive_result(content) is False


@pytest.mark.anyio
async def test_repeated_identical_search_is_refused_with_its_own_notice(tmp_path: Path) -> None:
    """Verbatim repeats get the repeat notice, and productive results do not mask it."""
    calls: list[str] = []

    async def docs_search(query: str) -> str:
        calls.append(query)
        return '{"items": [{"title": "spec"}]}'

    class _SameQueryProvider(_RewordingProvider):
        async def handler(self, request: web.Request) -> web.StreamResponse:
            self.requests.append(await request.json())
            turn = len(self.requests)
            chunk = (
                _tool_call_chunk("feishu_docs_search", json.dumps({"query": "same"}))
                if turn <= self._rounds
                else _stop_chunk("停")
            )
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp

    provider = _SameQueryProvider("feishu_docs_search", rounds=REPEAT_LIMIT + 3)
    requests = await _run_against(provider, "feishu_docs_search", docs_search)

    assert len(calls) == REPEAT_LIMIT, f"tool ran {len(calls)} times, expected {REPEAT_LIMIT}"
    notices = [c for req in requests for c in _tool_contents(req) if "未执行" in c]
    assert notices, "no repeat notice reached the model"
    assert "相同参数不会得到不同结果" in notices[0]
