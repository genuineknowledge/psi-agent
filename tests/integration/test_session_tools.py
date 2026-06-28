from __future__ import annotations

import json
from pathlib import Path

import pytest

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import ToolFunction
from psi_agent.session.tool_registry import ToolRegistry
from tests.integration.conftest import MockAIServer


def _chunk(
    content: str = "",
    reasoning: str = "",
    tool_calls: list | None = None,
    finish_reason: str | None = None,
) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning"] = reasoning
    if tool_calls:
        delta["tool_calls"] = tool_calls
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


_T = {"index": 0, "id": "c1", "type": "function"}


def _tc(name: str, args: str) -> dict:
    return {
        "index": 0,
        "id": "c1",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


@pytest.mark.anyio
async def test_tool_throws_exception_caught(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(content="Let me try...", finish_reason=None),
            _chunk(tool_calls=[_tc("echo", '{"message": "test"}')], finish_reason="tool_calls"),
            _chunk(content="After tool", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    async def bad_tool(**kwargs) -> str:
        raise RuntimeError("Simulated tool failure")

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(
        ai_client=AiClient(base_url), tool_registry=ToolRegistry(tools={"echo": tf}, funcs={"echo": bad_tool})
    )

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.reasoning or "" for c in chunks)
    assert "Simulated tool failure" in all_reasoning or "RuntimeError" in all_reasoning


@pytest.mark.anyio
async def test_tool_returns_int_converted_to_string(mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(tool_calls=[_tc("echo", '{"message":"x"}')], finish_reason="tool_calls"),
            _chunk(content="ok", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    async def int_tool(**kwargs) -> int:
        return 42

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(
        ai_client=AiClient(base_url), tool_registry=ToolRegistry(tools={"echo": tf}, funcs={"echo": int_tool})
    )

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.reasoning or "" for c in chunks)
    assert "42" in all_reasoning


@pytest.mark.anyio
async def test_tool_returns_none_converted(mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(tool_calls=[_tc("echo", '{"message":"x"}')], finish_reason="tool_calls"),
            _chunk(content="ok", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    async def none_tool(**kwargs) -> None:
        return None

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(
        ai_client=AiClient(base_url), tool_registry=ToolRegistry(tools={"echo": tf}, funcs={"echo": none_tool})
    )

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.reasoning or "" for c in chunks)
    assert "None" in all_reasoning


@pytest.mark.anyio
async def test_tool_no_parameters(mock_ai_server: MockAIServer) -> None:
    mock_ai_server.set_responses(
        [
            _chunk(content="direct", finish_reason="stop"),
        ]
    )

    async def ping() -> str:
        return "pong"

    tf = ToolFunction.from_callable(ping)
    assert tf.parameters["type"] == "object"
    assert tf.parameters["required"] == []
    assert tf.parameters["properties"] == {}


@pytest.mark.anyio
async def test_tool_list_string_parameter(mock_ai_server: MockAIServer) -> None:

    async def multi(commands: list[str]) -> str:
        """Execute multiple commands."""
        return "done"

    tf = ToolFunction.from_callable(multi)
    assert tf.parameters["properties"]["commands"]["type"] == "array"
    assert tf.parameters["properties"]["commands"]["items"]["type"] == "string"


@pytest.mark.anyio
async def test_max_tool_rounds_limit(mock_ai_server: MockAIServer) -> None:
    responses: list[str] = []
    for _ in range(15):
        responses.append(_chunk(tool_calls=[_tc("echo", '{"message":"loop"}')], finish_reason="tool_calls"))
    mock_ai_server.set_responses(responses)
    base_url = await mock_ai_server.start()

    async def echo_tool(message: str) -> str:
        return "echo"

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(
        ai_client=AiClient(base_url),
        tool_registry=ToolRegistry(tools={"echo": tf}, funcs={"echo": echo_tool}),
        max_tool_rounds=10,
    )

    chunks = []
    async for c in agent.run({"role": "user", "content": "loop"}):
        chunks.append(c)

    all_content = "".join(c.content or "" for c in chunks)
    assert "Max tool rounds" in all_content

    tool_call_count = sum(1 for c in chunks if c.reasoning and "Tool Call" in (c.reasoning or ""))
    assert tool_call_count <= agent._max_tool_rounds
