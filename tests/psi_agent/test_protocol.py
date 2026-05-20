from __future__ import annotations

import json

from psi_agent.protocol import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    ErrorResponse,
    Message,
    StreamChoice,
    ToolDef,
    ToolFunction,
)


def test_message_to_dict() -> None:
    msg = Message(role="user", content="hello")
    assert msg.to_dict() == {"role": "user", "content": "hello"}


def test_message_with_tool_calls() -> None:
    msg = Message(role="assistant", tool_calls=[{"id": "c1", "type": "function"}])
    d = msg.to_dict()
    assert d["role"] == "assistant"
    assert "tool_calls" in d
    assert "content" not in d


async def _bash_tool(command: str) -> str:
    """Execute a bash command."""
    ...


def test_tool_function_from_callable_basic() -> None:
    tf = ToolFunction.from_callable(_bash_tool)
    assert tf.name == "_bash_tool"
    assert "Execute a bash command" in tf.description
    assert tf.parameters["type"] == "object"
    assert "command" in tf.parameters["properties"]
    assert tf.parameters["properties"]["command"]["type"] == "string"
    assert "command" in tf.parameters["required"]


async def _search_tool(query: str, limit: int = 10) -> str:
    """Search for information.

    Args:
        query: The search query.
        limit: Max results to return.
    """
    ...


def test_tool_function_from_callable_with_default() -> None:
    tf = ToolFunction.from_callable(_search_tool)
    assert "query" in tf.parameters["required"]
    assert "limit" not in tf.parameters["required"]
    assert tf.parameters["properties"]["limit"]["type"] == "integer"
    assert tf.parameters["properties"]["limit"]["description"] == "Max results to return."


async def _ping_tool() -> str:
    ...


def test_tool_function_from_callable_no_docstring() -> None:
    tf = ToolFunction.from_callable(_ping_tool)
    assert tf.name == "_ping_tool"
    assert tf.parameters["required"] == []


def test_tool_def_to_dict() -> None:
    tf = ToolFunction(name="test", description="A test tool", parameters={"type": "object", "properties": {}, "required": []})
    td = ToolDef(function=tf)
    d = td.to_dict()
    assert d["type"] == "function"
    assert d["function"]["name"] == "test"


def test_chat_completion_request_serialization() -> None:
    req = ChatCompletionRequest(
        model="gpt-4",
        messages=[Message(role="user", content="hi")],
        stream=True,
    )
    data = json.loads(req.to_json())
    assert data["model"] == "gpt-4"
    assert data["messages"] == [{"role": "user", "content": "hi"}]
    assert data["stream"] is True


def test_chat_completion_request_with_tools() -> None:
    tf = ToolFunction(name="bash", description="Run command", parameters={"type": "object", "properties": {}, "required": []})
    req = ChatCompletionRequest(
        model="gpt-4",
        messages=[Message(role="user", content="run ls")],
        tools=[ToolDef(function=tf)],
        stream=True,
    )
    data = json.loads(req.to_json())
    assert len(data["tools"]) == 1
    assert data["tools"][0]["function"]["name"] == "bash"


def test_chat_completion_request_from_dict() -> None:
    data = {
        "model": "test",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    req = ChatCompletionRequest.from_dict(data)
    assert req.model == "test"
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"


def test_chat_completion_chunk_to_sse() -> None:
    chunk = ChatCompletionChunk(
        id="c1",
        model="test",
        choices=[StreamChoice(index=0, delta=DeltaMessage(content="Hello"))],
    )
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    parsed = json.loads(sse[6:].strip())
    assert parsed["choices"][0]["delta"]["content"] == "Hello"


def test_chat_completion_chunk_reasoning() -> None:
    chunk = ChatCompletionChunk(
        choices=[StreamChoice(index=0, delta=DeltaMessage(reasoning_content="Let me think..."))],
    )
    sse = chunk.to_sse()
    parsed = json.loads(sse[6:].strip())
    assert parsed["choices"][0]["delta"]["reasoning_content"] == "Let me think..."


def test_chat_completion_chunk_tool_calls_in_delta() -> None:
    chunk = ChatCompletionChunk(
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(
                    tool_calls=[{
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                    }]
                ),
            )
        ],
    )
    sse = chunk.to_sse()
    parsed = json.loads(sse[6:].strip())
    tc = parsed["choices"][0]["delta"]["tool_calls"]
    assert len(tc) == 1
    assert tc[0]["function"]["name"] == "bash"


def test_chat_completion_chunk_sse_done() -> None:
    assert ChatCompletionChunk.sse_done() == "data: [DONE]\n\n"


def test_chat_completion_chunk_from_openai_chunk() -> None:
    data = {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 123456,
        "model": "gpt-4",
        "choices": [{
            "index": 0,
            "delta": {"content": "Hello"},
            "finish_reason": None,
        }],
    }
    chunks = ChatCompletionChunk.from_openai_chunk(data)
    assert chunks is not None
    assert len(chunks) == 1
    assert chunks[0].choices[0].delta.content == "Hello"


def test_error_response_to_json() -> None:
    err = ErrorResponse(message="Something wrong", type="internal_error", code="500")
    data = json.loads(err.to_json())
    assert data["error"]["message"] == "Something wrong"
    assert data["error"]["type"] == "internal_error"
    assert data["error"]["code"] == "500"
