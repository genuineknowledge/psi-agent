from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.runtime_context import get_session_tool_context


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
        ctx = get_session_tool_context()
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
