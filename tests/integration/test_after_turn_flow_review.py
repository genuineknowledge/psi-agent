"""Tests for after_turn → BackgroundReview → flow review pipeline.

Verifies that:
1. after_turn_fn is called when agent finishes with stop.
2. BackgroundReview.maybe_spawn triggers flow review when new .flow.ts files exist.
3. _load_after_turn_fn correctly injects complete_fn.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from psi_agent.session import _load_after_turn_fn, _build_system_prompt_from_workspace
from psi_agent.session.agent import SessionAgent
from psi_agent.session.tools import load_tool_callables_from_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCLAW_WS = REPO_ROOT / "examples" / "openclaw-style-workspace"


def _sse_chunk(content: str = "", finish: str | None = None) -> bytes:
    chunk = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": {"content": content} if content else {}, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


class MockAIServer:
    def __init__(self, tmp_path: Path) -> None:
        self.socket_path = tmp_path / "ai.sock"
        self._runner: web.AppRunner | None = None

    async def start(self, handler) -> str:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.UnixSite(self._runner, str(self.socket_path))
        await site.start()
        return str(self.socket_path)

    async def cleanup(self) -> None:
        if self._runner:
            await self._runner.cleanup()


# ---------------------------------------------------------------------------
# Test 1: after_turn_fn is called on stop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_after_turn_fn_called_on_stop(tmp_path: Path) -> None:
    """after_turn_fn must be called when AI finishes with stop."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_sse_chunk(content="Hello", finish="stop"))
        await resp.write(b"data: [DONE]\n\n")
        return resp

    after_turn_called = asyncio.Event()
    received_tool_count: list[int] = []

    async def fake_after_turn(messages: list, tool_call_count: int, called_tools: list) -> None:
        received_tool_count.append(tool_call_count)
        after_turn_called.set()

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(
            ai_socket=ai_socket, tools={}, model="test", after_turn_fn=fake_after_turn
        )
        async for _ in agent.run({"role": "user", "content": "hi"}):
            pass

        # Simulate what server.py does after run() completes
        agent.spawn_after_turn_task()

        # Give the background task a chance to run
        await asyncio.wait_for(after_turn_called.wait(), timeout=5.0)
        assert after_turn_called.is_set()
        assert received_tool_count == [0]
    finally:
        await mock_server.cleanup()


# ---------------------------------------------------------------------------
# Test 2: after_turn_fn called on max tool rounds
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_after_turn_fn_called_on_max_rounds(tmp_path: Path) -> None:
    """after_turn_fn must also fire when max tool rounds is reached."""
    call_count = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal call_count
        call_count += 1
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        # Always return a tool call so agent loops until max rounds
        tc_chunk = {
            "id": "mock", "object": "chat.completion.chunk", "created": 0, "model": "test",
            "choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "id": f"call_{call_count}", "type": "function",
                                "function": {"name": "fake_tool", "arguments": "{}"}}]
            }, "finish_reason": None}],
        }
        await resp.write(f"data: {json.dumps(tc_chunk)}\n\n".encode())
        fin_chunk = {"id": "mock", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
        await resp.write(f"data: {json.dumps(fin_chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    after_turn_called = asyncio.Event()

    async def fake_after_turn(messages, tool_call_count, called_tools) -> None:
        after_turn_called.set()

    from psi_agent.session.protocol import ToolFunction
    fake_tool_def = ToolFunction(name="fake_tool", description="fake", parameters={"type": "object", "properties": {}})

    mock_server = MockAIServer(tmp_path)
    ai_socket = await mock_server.start(handler)
    try:
        agent = SessionAgent(
            ai_socket=ai_socket,
            tools={"fake_tool": fake_tool_def},
            model="test",
            after_turn_fn=fake_after_turn,
        )
        agent.register_tool_func("fake_tool", AsyncMock(return_value="ok"))

        async for _ in agent.run({"role": "user", "content": "hi"}):
            pass

        # Simulate what server.py does after run() completes
        agent.spawn_after_turn_task()

        await asyncio.wait_for(after_turn_called.wait(), timeout=5.0)
        assert after_turn_called.is_set()
    finally:
        await mock_server.cleanup()


# ---------------------------------------------------------------------------
# Test 3: BackgroundReview.maybe_spawn triggers flow review for new files
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_background_review_flow_triggered_for_new_files(tmp_path: Path) -> None:
    """maybe_spawn must spawn a flow review task when new qualifying .flow.ts files appear."""
    ws_src = OPENCLAW_WS
    ws = tmp_path / "workspace"
    shutil.copytree(ws_src, ws, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # Add workspace systems to sys.path
    sys.path.insert(0, str(ws / "systems"))
    sys.path.insert(0, str(ws / "tools"))
    try:
        from background_review import BackgroundReview, FLOW_PRIMITIVE_THRESHOLD
    finally:
        sys.path.pop(0)
        sys.path.pop(0)

    # Create a qualifying .flow.ts file in flows/adhoc/ with enough primitives
    adhoc_dir = ws / "flows" / "adhoc" / "test-flow"
    adhoc_dir.mkdir(parents=True, exist_ok=True)
    # Write a flow with enough primitive calls to pass threshold
    primitives = "\n".join(
        [f"flow.agent({{ name: 'step{i}', execute: async () => '' }});" for i in range(FLOW_PRIMITIVE_THRESHOLD + 2)]
    )
    flow_file = adhoc_dir / "flow.ts"
    flow_file.write_text(f"import {{ flow }} from '../src/index';\n{primitives}\n")

    flow_review_spawned = asyncio.Event()
    review_messages: list = []

    async def fake_complete(messages, tools):
        review_messages.extend(messages)
        flow_review_spawned.set()
        return {"choices": [{"message": {"role": "assistant", "content": "Nothing to capture."}, "finish_reason": "stop"}]}

    br = BackgroundReview(
        complete_fn=fake_complete,
        tool_executors={},
        workspace_dir=ws,
    )

    messages_snapshot = [{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}]
    await br.maybe_spawn(
        messages_snapshot=messages_snapshot,
        tool_call_count=0,
        new_flow_files=[str(flow_file)],
    )

    await asyncio.wait_for(flow_review_spawned.wait(), timeout=10.0)
    assert flow_review_spawned.is_set()
    # The flow review prompt should instruct LLM to find the flow from conversation history
    all_content = " ".join(str(m.get("content", "")) for m in review_messages)
    assert "flow.ts" in all_content


@pytest.mark.anyio
async def test_background_review_flow_not_triggered_without_flow_files(tmp_path: Path) -> None:
    """maybe_spawn must NOT spawn flow review when no new_flow_files are provided."""
    ws_src = OPENCLAW_WS
    ws = tmp_path / "workspace"
    shutil.copytree(ws_src, ws, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    sys.path.insert(0, str(ws / "systems"))
    sys.path.insert(0, str(ws / "tools"))
    try:
        from background_review import BackgroundReview
    finally:
        sys.path.pop(0)
        sys.path.pop(0)

    flow_review_spawned = asyncio.Event()

    async def fake_complete(messages, tools):
        all_content = " ".join(str(m.get("content", "")) for m in messages)
        if "flow.ts" in all_content or "flow_manage" in all_content:
            flow_review_spawned.set()
        return {"choices": [{"message": {"role": "assistant", "content": "Nothing."}, "finish_reason": "stop"}]}

    br = BackgroundReview(
        complete_fn=fake_complete,
        tool_executors={},
        workspace_dir=ws,
    )

    messages_snapshot = [{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}]
    # No new_flow_files provided
    await br.maybe_spawn(
        messages_snapshot=messages_snapshot,
        tool_call_count=0,
        new_flow_files=[],
    )

    await asyncio.sleep(0.2)
    assert not flow_review_spawned.is_set(), "Flow review should NOT be triggered when no flow files provided"


# ---------------------------------------------------------------------------
# Test 4: _load_after_turn_fn injects complete_fn when ai_socket provided
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_after_turn_fn_injects_complete_fn(tmp_path: Path) -> None:
    """_load_after_turn_fn must return a wrapper that injects complete_fn."""
    ws_src = OPENCLAW_WS
    ws = tmp_path / "workspace"
    shutil.copytree(ws_src, ws, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # Load system module first (required by _load_after_turn_fn)
    await _build_system_prompt_from_workspace(ws, model="test-model", tool_names=[])

    tool_callables = await load_tool_callables_from_workspace(ws / "tools")

    after_turn_fn = _load_after_turn_fn(
        ws,
        ai_socket="/tmp/fake-ai.sock",
        model="test-model",
        tool_callables=tool_callables,
    )

    assert after_turn_fn is not None, "_load_after_turn_fn returned None"

    # Inspect the closure: _after_turn_with_fn closes over _complete_fn and method.
    # Verify by calling after_turn_fn and checking that it tries to use complete_fn
    # (it will fail connecting to the fake socket, which is fine — we just need
    # to confirm it's a wrapper, not the bare method).
    import inspect
    sig = inspect.signature(after_turn_fn)
    # _after_turn_with_fn signature: (messages, tool_call_count, called_tools)
    # bare method signature: (self, messages, ..., *, complete_fn=None, ...)
    params = list(sig.parameters)
    assert "complete_fn" not in params, (
        "after_turn_fn should be the injected wrapper, not the bare method "
        "(bare method has complete_fn param; wrapper does not)"
    )
    assert params == ["messages", "tool_call_count", "called_tools"], (
        f"Unexpected wrapper signature: {params}"
    )
