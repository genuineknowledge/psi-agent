from __future__ import annotations

import json

from psi_agent._protocol import (
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
    return ""


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
    return ""


def test_tool_function_from_callable_with_default() -> None:
    tf = ToolFunction.from_callable(_search_tool)
    assert "query" in tf.parameters["required"]
    assert "limit" not in tf.parameters["required"]
    assert tf.parameters["properties"]["limit"]["type"] == "integer"
    assert tf.parameters["properties"]["limit"]["description"] == "Max results to return."


async def _ping_tool() -> str:
    return ""


def test_tool_function_from_callable_no_docstring() -> None:
    tf = ToolFunction.from_callable(_ping_tool)
    assert tf.name == "_ping_tool"
    assert tf.parameters["required"] == []


def test_tool_def_to_dict() -> None:
    tf = ToolFunction(
        name="test",
        description="A test tool",
        parameters={"type": "object", "properties": {}, "required": []},
    )
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
    tf = ToolFunction(
        name="bash",
        description="Run command",
        parameters={"type": "object", "properties": {}, "required": []},
    )
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
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
                        }
                    ]
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
        "choices": [
            {
                "index": 0,
                "delta": {"content": "Hello"},
                "finish_reason": None,
            }
        ],
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


# --- Missing coverage tests ---


def test_delta_message_to_dict() -> None:
    dm = DeltaMessage(content="hi", role="assistant")
    d = dm.to_dict()
    assert d == {"content": "hi", "role": "assistant"}


def test_delta_message_empty_to_dict() -> None:
    dm = DeltaMessage()
    assert dm.to_dict() == {}


def test_error_response_to_dict() -> None:
    err = ErrorResponse(message="msg", type="err", code="500")
    d = err.to_dict()
    assert d["error"]["message"] == "msg"


def test_python_type_to_json_float() -> None:
    assert ToolFunction._python_type_to_json_type(float) == "number"


def test_python_type_to_json_bool() -> None:
    assert ToolFunction._python_type_to_json_type(bool) == "boolean"


def test_python_type_to_json_unknown_fallback() -> None:
    assert ToolFunction._python_type_to_json_type(bytes) == "string"


def test_chat_completion_request_from_dict_with_tools() -> None:
    data = {
        "model": "test",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "t1", "description": "d", "parameters": {}}}],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 100,
    }
    req = ChatCompletionRequest.from_dict(data)
    assert req.model == "test"
    assert len(req.tools) == 1
    assert req.tools[0].function is not None
    assert req.tools[0].function.name == "t1"
    assert req.temperature == 0.7
    assert req.max_tokens == 100


def test_message_with_tool_call_id_and_name() -> None:
    msg = Message(role="tool", tool_call_id="c1", name="bash", content="result")
    d = msg.to_dict()
    assert d == {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "result"}


def test_tool_function_from_callable_all_defaults() -> None:
    async def f(a: str = "x", b: int = 1) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["required"] == []


def test_tool_function_from_callable_with_bool() -> None:
    async def f(verbose: bool = False) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["verbose"]["type"] == "boolean"


def test_parse_description_only() -> None:
    desc = ToolFunction._parse_description("Short description.")
    assert desc == "Short description."


def test_parse_description_with_args_stop() -> None:
    desc = ToolFunction._parse_description("First line.\nArgs:\n    x: something")
    assert desc == "First line."


def test_parse_param_descriptions_multiline() -> None:
    doc = "Args:\n    x: First line.\n        Continuation line."
    result = ToolFunction._parse_param_descriptions(doc)
    assert result["x"] == "First line. Continuation line."


def test_chat_completion_chunk_to_dict_direct() -> None:
    chunk = ChatCompletionChunk(
        id="c1",
        model="test",
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(content="ok", reasoning_content="think"),
                finish_reason="stop",
            )
        ],
    )
    d = chunk.to_dict()
    assert d["id"] == "c1"
    assert d["choices"][0]["delta"]["reasoning_content"] == "think"


def test_message_to_dict_omits_none_fields() -> None:
    msg = Message(role="user", content="hi", tool_calls=None, tool_call_id=None, name=None)
    d = msg.to_dict()
    assert "tool_calls" not in d
    assert "tool_call_id" not in d
    assert "name" not in d
