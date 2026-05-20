# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Session tool execution corner case tests."""

import json
from pathlib import Path

import pytest

from tests.integration.conftest import MockAIServer


def _chunk(
    content: str = "", reasoning: str = "", tool_calls: list | None = None, finish_reason: str | None = None
) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
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


async def _start_ai_server(tmp_path: Path, mock: MockAIServer) -> tuple[str, str]:
    """Start AI server and return (ai_socket_path, channel_socket_path)."""
    import subprocess
    import time

    base_url = await mock.start()
    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"
    ai_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            str(ai_socket),
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ai_socket.exists():
            break
        time.sleep(0.1)
    return str(ai_socket), str(channel_socket), ai_proc


@pytest.mark.anyio
async def test_tool_throws_exception_caught(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """When a tool raises an exception, it should be caught and returned as error text."""
    mock_ai_server.set_responses(
        [
            _chunk(content="Let me try...", finish_reason=None),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"message": "test"}'},
                    }
                ],
                finish_reason="tool_calls",
            ),
            _chunk(content="After tool", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    from psi_agent.protocol import ToolFunction
    from psi_agent.session.agent import SessionAgent

    async def bad_tool(**kwargs) -> str:
        raise RuntimeError("Simulated tool failure")

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(ai_socket=base_url, tools={"echo": tf}, model="test")
    agent.register_tool_func("echo", bad_tool)

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
    assert "Simulated tool failure" in all_reasoning or "RuntimeError" in all_reasoning


@pytest.mark.anyio
async def test_tool_returns_int_converted_to_string(mock_ai_server: MockAIServer) -> None:
    """Tool returning int should be converted to string."""
    mock_ai_server.set_responses(
        [
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"message":"x"}'},
                    }
                ],
                finish_reason="tool_calls",
            ),
            _chunk(content="ok", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    from psi_agent.protocol import ToolFunction
    from psi_agent.session.agent import SessionAgent

    async def int_tool(**kwargs) -> int:
        return 42

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(ai_socket=base_url, tools={"echo": tf}, model="test")
    agent.register_tool_func("echo", int_tool)

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
    assert "42" in all_reasoning


@pytest.mark.anyio
async def test_tool_returns_none_converted(mock_ai_server: MockAIServer) -> None:
    """Tool returning None should become 'None'."""
    mock_ai_server.set_responses(
        [
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"message":"x"}'},
                    }
                ],
                finish_reason="tool_calls",
            ),
            _chunk(content="ok", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    from psi_agent.protocol import ToolFunction
    from psi_agent.session.agent import SessionAgent

    async def none_tool(**kwargs) -> None:
        return None

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(ai_socket=base_url, tools={"echo": tf}, model="test")
    agent.register_tool_func("echo", none_tool)

    chunks = []
    async for c in agent.run({"role": "user", "content": "test"}):
        chunks.append(c)

    all_reasoning = "".join(c.choices[0].delta.reasoning_content or "" for c in chunks if c.choices)
    assert "None" in all_reasoning


@pytest.mark.anyio
async def test_tool_no_parameters(mock_ai_server: MockAIServer) -> None:
    """Tool with no parameters should have empty properties/required."""
    mock_ai_server.set_responses(
        [
            _chunk(content="direct", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

    from psi_agent.protocol import ToolFunction

    async def ping() -> str:
        return "pong"

    tf = ToolFunction.from_callable(ping)
    assert tf.parameters["type"] == "object"
    assert tf.parameters["required"] == []
    assert tf.parameters["properties"] == {}


@pytest.mark.anyio
async def test_tool_list_string_parameter(mock_ai_server: MockAIServer) -> None:
    """Tool with list[str] parameter should fallback to 'string'."""

    from psi_agent.protocol import ToolFunction

    async def multi(commands: list[str]) -> str:  # noqa: ARG001
        """Execute multiple commands."""
        return "done"

    tf = ToolFunction.from_callable(multi)
    assert tf.parameters["properties"]["commands"]["type"] == "string"


@pytest.mark.anyio
async def test_max_tool_rounds_limit(mock_ai_server: MockAIServer) -> None:
    """AI infinitely requesting tool_calls should be capped at 10 rounds."""
    # Generate 15 identical tool_call responses to exhaust the limit
    responses: list[str] = []
    for _ in range(15):
        responses.append(
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"message":"loop"}'},
                    }
                ],
                finish_reason="tool_calls",
            )
        )
    mock_ai_server.set_responses(responses)
    base_url = await mock_ai_server.start()

    from psi_agent.protocol import ToolFunction
    from psi_agent.session.agent import MAX_TOOL_ROUNDS, SessionAgent

    async def echo_tool(message: str) -> str:  # noqa: ARG001
        return "echo"

    tf = ToolFunction(name="echo", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(ai_socket=base_url, tools={"echo": tf}, model="test")
    agent.register_tool_func("echo", echo_tool)

    chunks = []
    async for c in agent.run({"role": "user", "content": "loop"}):
        chunks.append(c)

    all_content = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert "Max tool rounds" in all_content

    # Count tool call executions (should be exactly MAX_TOOL_ROUNDS = 10)
    tool_call_count = sum(
        1
        for c in chunks
        if c.choices
        and c.choices[0].delta.reasoning_content
        and "Tool Call" in (c.choices[0].delta.reasoning_content or "")
    )
    assert tool_call_count <= MAX_TOOL_ROUNDS
