from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from psi_agent.session._protocol import (
    ChatCompletionChunk,
    DeltaMessage,
    SessionToolContext,
    StreamChoice,
    ToolFunction,
)
from psi_agent.session.agent import SessionAgent


def _tool_call_chunk() -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="tool",
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "echo_tool", "arguments": "{}"},
                        }
                    ]
                ),
                finish_reason="tool_calls",
            )
        ],
    )


def _stop_chunk() -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="done",
        choices=[StreamChoice(index=0, delta=DeltaMessage(content="done"), finish_reason="stop")],
    )


class RuntimeContextSessionAgent(SessionAgent):
    async def _stream_ai_request(self, request_body: dict) -> AsyncIterator[ChatCompletionChunk]:
        _ = request_body
        yield _tool_call_chunk()
        yield _stop_chunk()


@pytest.mark.anyio
async def test_user_message_is_written_before_tool_execution(tmp_path: Path) -> None:
    history_path = tmp_path / "workspace" / "histories" / "session-1.jsonl"
    history_path.parent.mkdir(parents=True)

    seen: dict[str, object] = {}

    async def echo_tool() -> str:
        ctx = SessionToolContext.current()
        assert ctx is not None
        seen["ctx"] = ctx
        seen["history_file"] = history_path.read_text()
        return "ok"

    tf = ToolFunction(
        name="echo_tool",
        description="Echo",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    agent = RuntimeContextSessionAgent(
        ai_socket="http://127.0.0.1:1",
        tools={"echo_tool": tf},
        tool_funcs={"echo_tool": echo_tool},
        history=[],
        history_path=history_path,
    )

    async for _ in agent.run({"role": "user", "content": "hello"}):
        pass

    assert seen["ctx"] is not None
    assert '"role": "user"' in str(seen["history_file"])
    assert '"content": "hello"' in str(seen["history_file"])


@pytest.mark.anyio
async def test_tool_context_history_is_isolated_from_session_mutation(tmp_path: Path) -> None:
    history_path = tmp_path / "workspace" / "histories" / "session-1.jsonl"
    history_path.parent.mkdir(parents=True)
    user_message = {"role": "user", "content": "hello"}

    async def mutating_tool() -> str:
        ctx = SessionToolContext.current()
        assert ctx is not None
        with pytest.raises(TypeError):
            cast(dict[str, Any], ctx.history_messages[0])["content"] = "mutated via history"
        with pytest.raises(TypeError):
            cast(dict[str, Any], ctx.latest_user_message)["content"] = "mutated via latest"
        return "ok"

    tf = ToolFunction(
        name="echo_tool",
        description="Echo",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    agent = RuntimeContextSessionAgent(
        ai_socket="http://127.0.0.1:1",
        tools={"echo_tool": tf},
        tool_funcs={"echo_tool": mutating_tool},
        history=[],
        history_path=history_path,
    )

    async for _ in agent.run(user_message):
        pass

    assert agent.history[0]["content"] == "hello"
    assert user_message["content"] == "hello"
