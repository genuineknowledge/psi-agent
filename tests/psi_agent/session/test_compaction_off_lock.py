"""Compaction runs off the session lock (件四).

The point of the card is *latency of the next message*, not compaction itself.
Compaction is an LLM call measured at 41.5s that used to run while the session
lock was held, so it was charged to whoever asked next (queueing p50 169s, and
774s on 2026-08-31) even though the finished turn gained nothing from it — it
runs after the reply is already streamed and committed.

So the central test here does not assert "the summary is correct".  It asserts
that a second turn *completes* while a first turn's compaction is still blocked
mid-call.  Under the old shape that second turn could not even start.

These tests drive ``turn_lock`` + ``run`` directly rather than going through
aiohttp: that pair is exactly what all four production lock sites use
(``handle_request``, ``handle_event``, ``schedule_registry``, ``live_agent``),
and a real socket would add a second serialization point that could mask the
lock behaviour under test.
"""

from __future__ import annotations

import socket
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.history_display import COMPACTED_COVERS_KEY, project_history_for_wire
from psi_agent.session.system_prompt import SystemPrompt

_STOP_SSE = (
    b'data: {"id":"test","choices":[{"index":0,"delta":{"content":"reply"},'
    b'"finish_reason":"stop"}],"created":0,"model":"test",'
    b'"object":"chat.completion.chunk"}\n\n'
)
_COMPACTION_SSE = (
    b'data: {"id":"compaction","choices":[{"index":0,"delta":{},'
    b'"finish_reason":"compaction_needed"}],'
    b'"psi_compaction":{"needed":true,"prompt_tokens":50000,"threshold":10000}}\n\n'
)


async def _serve_stop_then_signal(signal: bool) -> tuple[web.AppRunner, int]:
    """An AI stub that finishes a turn, optionally raising the compaction signal."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        await resp.write(_STOP_SSE)
        if signal:
            await resp.write(_COMPACTION_SSE)
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    await web.SockSite(runner, s).start()
    await anyio.sleep(0.05)
    return runner, port


def _agent(port: int, compaction_fn: Any, messages: list[dict[str, Any]] | None = None) -> SessionAgent:
    conv = Conversation(
        messages=messages
        if messages is not None
        else [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "old 1"},
            {"role": "assistant", "content": "old reply 1"},
        ]
    )
    return SessionAgent(
        ai_client=AiClient(ai_socket=f"http://127.0.0.1:{port}"),
        conversation=conv,
        system_prompt=SystemPrompt(builder=lambda: "You are helpful.", compaction_fn=compaction_fn),
    )


async def _turn(agent: SessionAgent, content: str) -> None:
    """One turn through the same guard production uses."""
    async with agent.turn_lock():
        async for _ in agent.run({"role": "user", "content": content}):
            pass


@pytest.mark.anyio
async def test_new_message_not_blocked_while_compaction_runs() -> None:
    """The whole point of the card.

    Turn 1 raises the signal and its compaction blocks on ``blocked``.  Turn 2
    must run to completion while it is still blocked.  If compaction were still
    inside the lock, turn 2 would wait on ``turn_lock`` and this deadlocks —
    hence the ``fail_after``, which is the assertion.
    """
    runner, port = await _serve_stop_then_signal(signal=True)
    compaction_entered = anyio.Event()
    release = anyio.Event()
    turn2_done_before_release = False

    async def slow_compaction(history: list[dict[str, Any]], complete_fn: Any) -> str:
        compaction_entered.set()
        await release.wait()
        return "summary"

    try:
        agent = _agent(port, slow_compaction)

        async with anyio.create_task_group() as tg:

            async def turn1() -> None:
                await _turn(agent, "first")

            tg.start_soon(turn1)
            # Wait until compaction is genuinely in-flight, so the lock state
            # under test is the real one rather than a lucky interleaving.
            with anyio.fail_after(5):
                await compaction_entered.wait()

            # Compaction is mid-call. A second turn must get through.
            with anyio.fail_after(5):
                await _turn(agent, "second")
            assert not release.is_set(), "compaction should still be blocked"
            turn2_done_before_release = True
            release.set()

        assert turn2_done_before_release
        # Turn 2's user message and reply are really in the history.
        contents = [m.get("content") for m in agent._conversation.messages]
        assert "second" in contents
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_history_grown_during_compaction_is_not_deleted() -> None:
    """Rows that land while compaction runs survive projection.

    The summary was generated from a snapshot that never saw them, so cutting at
    the summary row's own index would drop them from the wire while leaving them
    in the JSONL — silent history loss.  ``covers`` records the snapshot
    boundary, so the cut lands there instead.
    """
    runner, port = await _serve_stop_then_signal(signal=True)
    compaction_entered = anyio.Event()
    release = anyio.Event()

    async def slow_compaction(history: list[dict[str, Any]], complete_fn: Any) -> str:
        compaction_entered.set()
        await release.wait()
        return "summary of the old part"

    try:
        agent = _agent(port, slow_compaction)

        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: _turn(agent, "first"))
            with anyio.fail_after(5):
                await compaction_entered.wait()
            with anyio.fail_after(5):
                await _turn(agent, "during-compaction")
            release.set()

        messages = agent._conversation.messages
        compacted = [m for m in messages if m.get("role") == "compacted"]
        assert len(compacted) == 1
        covers = compacted[0][COMPACTED_COVERS_KEY]

        # The boundary really is behind the late rows, i.e. this test is
        # exercising the interesting case and not a coincidence.
        assert covers < len(messages) - 1

        wire = project_history_for_wire(messages)
        wire_text = "\n".join(str(m.get("content", "")) for m in wire)
        assert "during-compaction" in wire_text, "row that arrived during compaction was deleted"
        assert "summary of the old part" in wire[0]["content"]
        assert wire[0]["role"] == "system"
        # The pre-snapshot rows are gone — the summary replaced them.
        assert "old 1" not in wire_text
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_compaction_failure_does_not_break_the_turn() -> None:
    """A failed compaction costs the summary, not the turn.

    件一A made elision the thing that guarantees a legal request, so compaction
    is now quality-only.  This pins that: the drain swallows the error, the reply
    is intact, no ``compacted`` row is written, and the session keeps taking
    turns afterwards.
    """
    runner, port = await _serve_stop_then_signal(signal=True)

    async def failing_compaction(history: list[dict[str, Any]], complete_fn: Any) -> str:
        raise RuntimeError("compaction upstream is down")

    try:
        agent = _agent(port, failing_compaction)

        with anyio.fail_after(5):
            await _turn(agent, "first")

        messages = agent._conversation.messages
        assert not [m for m in messages if m.get("role") == "compacted"]
        assert "reply" in [m.get("content") for m in messages]

        # And the session is still usable — a failure must not wedge the lock.
        with anyio.fail_after(5):
            await _turn(agent, "second")
        assert "second" in [m.get("content") for m in agent._conversation.messages]
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_concurrent_drains_write_one_summary() -> None:
    """Two overlapping drains must not both summarize.

    Two ``compacted`` rows would mean one paid-for LLM call discarded by the
    projection (it takes the last row), and if their coverage boundaries
    differed, the surviving row's boundary could cut away rows the surviving
    summary never described.
    """
    runner, port = await _serve_stop_then_signal(signal=True)
    calls = 0
    first_entered = anyio.Event()
    release = anyio.Event()

    async def counting_compaction(history: list[dict[str, Any]], complete_fn: Any) -> str:
        nonlocal calls
        calls += 1
        first_entered.set()
        await release.wait()
        return f"summary {calls}"

    try:
        agent = _agent(port, counting_compaction)
        agent._request_compaction(50000, 10000)

        async with anyio.create_task_group() as tg:
            tg.start_soon(agent.drain_pending_compaction)
            with anyio.fail_after(5):
                await first_entered.wait()
            # Second drain enters while the first is still in its LLM call.
            agent._request_compaction(50000, 10000)
            tg.start_soon(agent.drain_pending_compaction)
            await anyio.sleep(0.05)
            release.set()

        assert calls == 1, f"compaction ran {calls} times concurrently"
        compacted = [m for m in agent._conversation.messages if m.get("role") == "compacted"]
        assert len(compacted) == 1
    finally:
        await runner.cleanup()
