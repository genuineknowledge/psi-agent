from __future__ import annotations

import json
from typing import Any, cast

import pytest

from psi_agent.session._protocol import (
    ChatCompletionChunk,
    DeltaMessage,
    StreamChoice,
    ToolFunction,
)


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


def test_chat_completion_chunk_to_sse() -> None:
    chunk = ChatCompletionChunk(
        id="c1",
        choices=[StreamChoice(index=0, delta=DeltaMessage(content="Hello"))],
    )
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    parsed = json.loads(sse[6:].strip())
    assert parsed["choices"][0]["delta"]["content"] == "Hello"


def test_chat_completion_chunk_reasoning() -> None:
    chunk = ChatCompletionChunk(
        choices=[StreamChoice(index=0, delta=DeltaMessage(reasoning="Let me think..."))],
    )
    sse = chunk.to_sse()
    parsed = json.loads(sse[6:].strip())
    assert parsed["choices"][0]["delta"]["reasoning"] == "Let me think..."


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


# --- Missing coverage tests ---


def test_delta_message_to_dict() -> None:
    dm = DeltaMessage(content="hi", role="assistant")
    d = dm.to_dict()
    assert d == {"content": "hi", "role": "assistant"}


def test_delta_message_empty_to_dict() -> None:
    dm = DeltaMessage()
    assert dm.to_dict() == {}


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
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(content="ok", reasoning="think"),
                finish_reason="stop",
            )
        ],
    )
    d = chunk.to_dict()
    assert d["id"] == "c1"
    assert d["choices"][0]["delta"]["reasoning"] == "think"


def test_tool_function_optional_none_type() -> None:
    """X | None should be unwrapped to X and marked as not-required."""

    async def f(verbose: bool | None = None) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["verbose"]["type"] == "boolean"
    assert "verbose" not in tf.parameters["required"]


def test_tool_function_unsupported_type_raises() -> None:
    async def f(data: bytes) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported parameter type"):
        ToolFunction.from_callable(f)


def test_tool_function_multi_type_union_raises() -> None:
    """int | str should be rejected."""

    async def f(flag: int | str) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported union type"):
        ToolFunction.from_callable(f)


def test_tool_function_multi_type_union_with_none_raises() -> None:
    """int | str | None should be rejected."""

    async def f(flag: int | str | None) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported union type"):
        ToolFunction.from_callable(f)


def test_tool_function_list_int_parameter() -> None:
    """list[int] should be resolved to array of integer."""

    async def f(values: list[int]) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["values"]["type"] == "array"
    assert tf.parameters["properties"]["values"]["items"]["type"] == "integer"


def test_tool_function_list_unsupported_item_raises() -> None:
    """list[bytes] should be rejected."""

    async def f(data: list[bytes]) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported list item type"):
        ToolFunction.from_callable(f)


def test_tool_function_optional_without_default() -> None:
    """X | None with NO default value should be not-required."""

    async def f(verbose: bool | None) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["verbose"]["type"] == "boolean"
    assert "verbose" not in tf.parameters["required"]


def test_tool_function_float_parameter() -> None:
    """float should be mapped to number."""

    async def f(temperature: float = 0.7) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["temperature"]["type"] == "number"


def test_tool_function_ignores_self_param() -> None:
    """self parameter should not appear in the tool definition."""

    async def f(self, command: str) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert "self" not in tf.parameters["properties"]
    assert "command" in tf.parameters["properties"]


def test_parse_description_stops_at_yields() -> None:
    """Description should stop at Yields section, like Args and Returns."""

    desc = ToolFunction._parse_description("Short description.\nYields:\n    x: something")
    assert desc == "Short description."


def test_parse_description_stops_at_returns() -> None:
    """Description should stop at Returns section."""

    desc = ToolFunction._parse_description("Short description.\n\nReturns:\n    str")
    assert desc == "Short description."


def test_stream_choice_to_dict() -> None:
    sc = StreamChoice(index=0, delta=DeltaMessage(content="hi"), finish_reason="stop")
    d = sc.to_dict()
    assert d["index"] == 0
    assert d["delta"]["content"] == "hi"
    assert d["finish_reason"] == "stop"


def test_stream_choice_to_dict_no_finish_reason() -> None:
    sc = StreamChoice(index=1, delta=DeltaMessage(tool_calls=[{"index": 0}]))
    d = sc.to_dict()
    assert d["index"] == 1
    assert d["delta"]["tool_calls"] == [{"index": 0}]
    assert "finish_reason" not in d


def test_tool_function_ignores_variadic() -> None:
    """*args and **kwargs should raise TypeError."""

    async def f(command: str, *args: str) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Variadic parameters"):
        ToolFunction.from_callable(f)


def test_tool_function_kwargs_raises() -> None:
    """**kwargs should raise TypeError."""

    async def f(**kwargs: int) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Variadic parameters"):
        ToolFunction.from_callable(f)


# --- Additional coverage tests ---


def test_delta_message_reasoning_only() -> None:
    dm = DeltaMessage(reasoning="think")
    assert dm.to_dict() == {"reasoning": "think"}


def test_delta_message_tool_calls_only() -> None:
    dm = DeltaMessage(tool_calls=[{"index": 0}])
    assert dm.to_dict() == {"tool_calls": [{"index": 0}]}


def test_delta_message_all_fields() -> None:
    dm = DeltaMessage(content="hi", role="assistant", reasoning="think", tool_calls=[{"index": 0}])
    d = dm.to_dict()
    assert d == {"content": "hi", "role": "assistant", "reasoning": "think", "tool_calls": [{"index": 0}]}


def test_delta_message_rejects_legacy_reasoning_content_field() -> None:
    with pytest.raises(TypeError):
        cast(Any, DeltaMessage)(reasoning_content="think")


def test_stream_choice_default() -> None:
    sc = StreamChoice()
    d = sc.to_dict()
    assert d == {"index": 0, "delta": {}}


def test_tool_function_list_float_parameter() -> None:
    """list[float] should be array of number."""

    async def f(vals: list[float]) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["vals"]["type"] == "array"
    assert tf.parameters["properties"]["vals"]["items"]["type"] == "number"


def test_tool_function_list_bool_parameter() -> None:
    """list[bool] should be array of boolean."""

    async def f(flags: list[bool]) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["flags"]["items"]["type"] == "boolean"


def test_tool_function_cls_skip() -> None:
    """cls parameter should be skipped."""

    async def f(cls, command: str) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert "cls" not in tf.parameters["properties"]
    assert "command" in tf.parameters["properties"]


def test_tool_function_no_annotation_defaults_string() -> None:
    """Missing type annotation should default to 'string'."""

    async def f(x) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["x"]["type"] == "string"


def test_tool_function_dict_generic_raises() -> None:
    """dict type should raise TypeError."""

    async def f(data: dict[str, int]) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported generic type"):
        ToolFunction.from_callable(f)


def test_tool_function_nested_list_raises() -> None:
    """list[list[int]] should raise TypeError."""

    async def f(matrix: list[list[int]]) -> str:
        return "ok"

    with pytest.raises(TypeError, match="Unsupported list item type"):
        ToolFunction.from_callable(f)


def test_tool_function_keyword_only_required() -> None:
    """Keyword-only param without default should be required."""

    async def f(*, name: str) -> str:
        return "ok"

    tf = ToolFunction.from_callable(f)
    assert tf.parameters["properties"]["name"]["type"] == "string"
    assert "name" in tf.parameters["required"]


def test_parse_description_blank_lines() -> None:
    """Blank lines between description paragraphs should be ignored."""

    desc = ToolFunction._parse_description("First.\n\nSecond.\nArgs:\n    x: y")
    assert desc == "First. Second."


def test_parse_param_descriptions_stops_at_returns() -> None:
    """Returns section should stop Args parsing."""

    result = ToolFunction._parse_param_descriptions("Args:\n    x: first\nReturns:\n    str")
    assert result == {"x": "first"}


def test_parse_param_descriptions_empty_args() -> None:
    """Empty Args section should return empty dict."""

    result = ToolFunction._parse_param_descriptions("Args:")
    assert result == {}


def test_parse_param_descriptions_empty_value() -> None:
    """Parameter with no description text after colon."""

    result = ToolFunction._parse_param_descriptions("Args:\n    x:")
    assert result == {"x": ""}
