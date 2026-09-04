"""Criteria for the per-turn elision exemption, driven through ``agent.run``.

These live at the **agent** layer on purpose.  ``test_request_assembly.py``
already pins the exemption's semantics given a watermark; what cannot be tested
there is whether a real turn ever sets one, and whether it clears it again.  A
criterion that called ``RequestAssembler`` directly would stay green even if
``agent.py`` never wired the watermark up at all — which is exactly the bug.

The production failure being forbidden (observed 2026-09, gateway log line 5874):
the assistant row written in round 1 of a multi-round turn stopped being covered
by the ``paired[:-2]`` guard once tool rows piled up behind it, so round 3
replaced it with an elision handle.  The upstream then saw a turn in which the
assistant had said nothing, apologised ("上一条消息里发送标记被截断了... 现在补发:")
and re-sent — and the re-send was elided the same way.  The user never got the
document.
"""

from __future__ import annotations

import json
import socket as _s
from contextlib import aclosing
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import AgentError
from psi_agent.session.request_assembly import RequestAssembler
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

_SEND_MARKER = "[SEND:/workspace/交付文档.md]"


def _sse(delta: dict[str, Any], finish: str | None) -> bytes:
    chunk = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


def _tool_call_sse(call_id: str, name: str, content: str = "") -> bytes:
    delta: dict[str, Any] = {
        "tool_calls": [
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ]
    }
    if content:
        delta["content"] = content
    return _sse(delta, "tool_calls")


class _Upstream:
    """Mock AI endpoint that records every request body it is asked to serve."""

    def __init__(self, scripted: list[bytes]) -> None:
        self._scripted = scripted
        self.bodies: list[dict[str, Any]] = []
        self._runner: web.AppRunner | None = None

    async def start(self) -> str:
        async def handler(request: web.Request) -> web.StreamResponse:
            self.bodies.append(await request.json())
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            index = min(len(self.bodies) - 1, len(self._scripted) - 1)
            await resp.write(self._scripted[index])
            await resp.write(b"data: [DONE]\n\n")
            return resp

        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        await web.SockSite(self._runner, sock).start()
        return f"http://127.0.0.1:{port}"

    async def cleanup(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    def wire_text(self, request_index: int) -> str:
        """All string content the upstream saw in one request."""
        return "".join(
            m["content"] for m in self.bodies[request_index]["messages"] if isinstance(m.get("content"), str)
        )


async def _bulky_tool() -> str:
    """A tool whose result is big enough to force elision."""
    return "工具结果 " + "乙" * 6000


def _tool_registry() -> ToolRegistry:
    tf = ToolFunction(
        name="bulky",
        description="Returns a large result.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    return ToolRegistry(files={"__test__": FileEntry(file_hash="", tools={"bulky": tf}, funcs={"bulky": _bulky_tool})})


def _seeded_history(tmp_path: Path, rows: int = 24) -> Conversation:
    """A session already carrying enough history that the budget bites."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "You are an agent."}]
    for i in range(rows):
        messages.append({"role": "user", "content": f"q{i} " + "问" * 2000})
        messages.append({"role": "assistant", "content": f"a{i} " + "答" * 2000})
    return Conversation(messages=messages, path=tmp_path / "histories" / "s.jsonl")


def _agent(socket: str, conversation: Conversation, *, max_tool_rounds: int = 20) -> SessionAgent:
    return SessionAgent(
        ai_client=AiClient(socket),
        tool_registry=_tool_registry(),
        conversation=conversation,
        max_tool_rounds=max_tool_rounds,
        request_assembler=RequestAssembler(max_context_tokens=20_000),
    )


@pytest.mark.anyio
async def test_round_one_assistant_row_is_not_elided_by_round_three(tmp_path: Path) -> None:
    """The production mechanism, reproduced end to end.

    Rounds 1 and 2 ask for tools, round 3 stops.  By round 3 the assistant row
    from round 1 has four rows behind it, so ``paired[:-2]`` no longer protects
    it.  The assertion is on the third request's wire text: the round-1 sentence
    must still be there in full, not behind a handle.
    """
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    round_one = "先查一下资料 " + "甲" * 3000
    upstream = _Upstream(
        [
            _tool_call_sse("c1", "bulky", content=round_one),
            _tool_call_sse("c2", "bulky", content="再查一次 " + "丙" * 3000),
            _sse({"content": "结论如上"}, "stop"),
        ]
    )
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path))
        _ = [c async for c in agent.run({"role": "user", "content": "帮我出个文档"})]

        assert len(upstream.bodies) == 3, "fixture must reach a third round"
        third = upstream.wire_text(2)
        assert round_one in third, "round 1's assistant output was elided before the turn finished"
        # The turn really was under elision pressure, so the exemption is what
        # spared the row rather than the budget never biting.
        assert "[已省略" in third, "fixture stopped exercising elision"
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_send_marker_reaches_the_upstream_on_a_later_round(tmp_path: Path) -> None:
    """The user-visible symptom: the delivered file's marker must not vanish.

    ``[SEND:]`` is the Channel's instruction to hand a file over.  When the row
    carrying it was elided, the upstream concluded nothing had been sent and
    re-sent forever, so the document never arrived.  Asserted on the marker
    itself, not on "a row was spared" — the marker is what the user lost.
    """
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    upstream = _Upstream(
        [
            _tool_call_sse("c1", "bulky", content="文档写好了 " + "甲" * 3000 + _SEND_MARKER),
            _tool_call_sse("c2", "bulky", content="再核对一遍 " + "丙" * 3000),
            _sse({"content": "已发送"}, "stop"),
        ]
    )
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path))
        _ = [c async for c in agent.run({"role": "user", "content": "帮我出个文档"})]

        assert len(upstream.bodies) == 3
        third = upstream.wire_text(2)
        assert _SEND_MARKER in third, "the [SEND:] marker was elided out of the request"
        assert "[已省略" in third, "fixture stopped exercising elision"
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_schedule_turns_get_the_same_exemption(tmp_path: Path) -> None:
    """Timed/triggered turns run the same loop, so they need the same protection.

    The ``schedule.`` branch skips the before-turn hook only; the watermark is set
    below that branch and the tool loop is shared.  A scheduled turn that produced
    a document would otherwise lose it exactly the same way.
    """
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    scheduled_output = "定时产出 " + "甲" * 3000 + _SEND_MARKER
    upstream = _Upstream(
        [
            _tool_call_sse("c1", "bulky", content=scheduled_output),
            _tool_call_sse("c2", "bulky", content="继续 " + "丙" * 3000),
            _sse({"content": "完成"}, "stop"),
        ]
    )
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path))
        _ = [
            c
            async for c in agent.run(
                {"role": "user", "content": "定时任务"},
                response_kind="schedule.display",
            )
        ]

        assert len(upstream.bodies) == 3
        third = upstream.wire_text(2)
        assert scheduled_output in third
        assert "[已省略" in third, "fixture stopped exercising elision"
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_watermark_is_reset_after_a_normal_turn(tmp_path: Path) -> None:
    """Baseline for the three failure exits below."""
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    upstream = _Upstream([_sse({"content": "done"}, "stop")])
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path, rows=2))
        _ = [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert agent._request_assembler._turn_watermark is None
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_watermark_is_reset_when_the_turn_raises(tmp_path: Path) -> None:
    """A raised turn must not leave the exemption pinned.

    The assembler is per session, so a watermark left behind would cap every
    later turn's elision range at this turn's index — a budget that silently
    stops being enforceable, with nothing in the log naming the cause.
    """
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    upstream = _Upstream([_sse({"content": "上游炸了"}, "error")])
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path, rows=2))
        with pytest.raises(AgentError):
            _ = [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert agent._request_assembler._turn_watermark is None
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_watermark_is_reset_when_the_turn_is_cancelled(tmp_path: Path) -> None:
    """anyio cancellation is the exit a ``return``-only cleanup would miss.

    Cancelled *the way every call site cancels*: inside ``aclosing``.  That
    detail is load-bearing rather than ceremony — an async generator abandoned
    without being closed does not run its ``finally`` until the event loop gets
    around to collecting it, so a bare ``async for`` under ``move_on_after``
    observes a watermark that is still set and blames the implementation for it.
    ``live_agent.resume_turn`` and the Channel path both use ``aclosing``, so
    closure is guaranteed at the boundary this criterion speaks for.
    """
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    upstream = _Upstream([_tool_call_sse("c1", "bulky", content="开始 " + "甲" * 3000)])
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path, rows=2))

        with anyio.move_on_after(0.01):
            async with aclosing(agent.run({"role": "user", "content": "hi"})) as chunks:
                async for _ in chunks:
                    await anyio.sleep(0.05)

        assert agent._request_assembler._turn_watermark is None
    finally:
        await upstream.cleanup()


@pytest.mark.anyio
async def test_watermark_is_reset_when_max_tool_rounds_is_hit(tmp_path: Path) -> None:
    """Exhausting the loop leaves via the ``for/else``, not via ``return``."""
    await anyio.Path(tmp_path / "histories").mkdir(parents=True)
    upstream = _Upstream([_tool_call_sse("c1", "bulky", content="还要再查 " + "甲" * 2000)])
    socket = await upstream.start()
    try:
        agent = _agent(socket, _seeded_history(tmp_path, rows=2), max_tool_rounds=2)
        _ = [c async for c in agent.run({"role": "user", "content": "hi"})]

        assert agent._request_assembler._turn_watermark is None
    finally:
        await upstream.cleanup()
