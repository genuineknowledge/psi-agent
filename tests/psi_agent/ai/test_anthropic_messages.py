from __future__ import annotations

import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, UnixConnector, web

from psi_agent.ai.anthropic_messages import AnthropicMessages


def test_cli_dataclass() -> None:
    config = AnthropicMessages(
        session_socket="/tmp/test.sock",
        model="claude-sonnet",
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com",
    )
    assert config.model == "claude-sonnet"
    assert config.verbose is False


@pytest.mark.anyio
async def test_anthropic_thinking_conversion(tmp_path: Path) -> None:
    socket_path = tmp_path / "ai.sock"

    async def mock_anthropic_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body["model"] == "claude-sonnet"

        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        event_data = json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            }
        )
        await resp.write(f"event: content_block_start\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Let me think about this..."},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " The answer is 42."},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    mock_app = web.Application()
    mock_app.router.add_post("/v1/messages", mock_anthropic_handler)

    runner = web.AppRunner(mock_app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    try:
        config = AnthropicMessages(
            session_socket=str(socket_path),
            model="claude-sonnet",
            api_key="sk-ant-test",
            base_url=f"http://127.0.0.1:{port}/v1",
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(config.run)
            await anyio.sleep(0.2)

            connector = UnixConnector(path=str(socket_path))
            async with ClientSession(connector=connector) as session:
                req_data = {
                    "model": "claude-sonnet",
                    "messages": [{"role": "user", "content": "what is 6*7"}],
                    "stream": True,
                }
                async with session.post("http://localhost/v1/chat/completions", json=req_data) as resp:
                    assert resp.status == 200
                    chunks: list[str] = []
                    async for raw in resp.content:
                        chunk = raw.decode().strip()
                        if chunk.startswith("data: ") and chunk != "data: [DONE]":
                            chunks.append(chunk)
                    all_text = "".join(chunks)
                    assert "reasoning_content" in all_text
                    assert "Let me think" in all_text
                    assert "The answer is 42" in all_text

            tg.cancel_scope.cancel()

    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_anthropic_tool_use_conversion(tmp_path: Path) -> None:
    socket_path = tmp_path / "ai.sock"

    async def mock_anthropic_handler(request: web.Request) -> web.StreamResponse:
        _body = await request.json()  # verify parsing succeeds
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)

        event_data = json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool_1", "name": "bash", "input": {}},
            }
        )
        await resp.write(f"event: content_block_start\ndata: {event_data}\n\n".encode())

        event_data = json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls"}'},
            }
        )
        await resp.write(f"event: content_block_delta\ndata: {event_data}\n\n".encode())

        await resp.write(b"event: message_stop\ndata: {}\n\n")
        return resp

    mock_app = web.Application()
    mock_app.router.add_post("/v1/messages", mock_anthropic_handler)

    runner = web.AppRunner(mock_app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    try:
        config = AnthropicMessages(
            session_socket=str(socket_path),
            model="claude-sonnet",
            api_key="sk-ant-test",
            base_url=f"http://127.0.0.1:{port}/v1",
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(config.run)
            await anyio.sleep(0.2)

            connector = UnixConnector(path=str(socket_path))
            async with ClientSession(connector=connector) as session:
                req_data = {
                    "model": "claude-sonnet",
                    "messages": [{"role": "user", "content": "run ls"}],
                    "stream": True,
                }
                async with session.post("http://localhost/v1/chat/completions", json=req_data) as resp:
                    assert resp.status == 200
                    chunks: list[str] = []
                    async for raw in resp.content:
                        chunk = raw.decode().strip()
                        if chunk.startswith("data: ") and chunk != "data: [DONE]":
                            chunks.append(chunk)
                    all_text = "".join(chunks)
                    assert "tool_calls" in all_text or "bash" in all_text

            tg.cancel_scope.cancel()

    finally:
        await runner.cleanup()


# --- Unit tests for Anthropic protocol conversion functions ---

from psi_agent.ai.anthropic_messages.server import (  # noqa: E402
    _convert_openai_messages_to_anthropic,
    _convert_openai_tools_to_anthropic,
)


def test_convert_system_message_to_anthropic() -> None:
    messages = [{"role": "system", "content": "You are helpful."}]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert result == []
    assert _system == ["You are helpful."]


def test_convert_multiple_system_messages() -> None:
    messages = [
        {"role": "system", "content": "Be polite."},
        {"role": "system", "content": "Be concise."},
    ]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert result == []
    assert _system == ["Be polite.", "Be concise."]


def test_convert_user_message() -> None:
    messages = [{"role": "user", "content": "hello"}]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert result == [{"role": "user", "content": "hello"}]
    assert _system == []


def test_convert_assistant_message() -> None:
    messages = [{"role": "assistant", "content": "Hi there!"}]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert result == [{"role": "assistant", "content": "Hi there!"}]
    assert _system == []


def test_convert_assistant_tool_calls() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        }
    ]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert isinstance(result[0]["content"], list)
    assert result[0]["content"][0]["type"] == "tool_use"
    assert result[0]["content"][0]["name"] == "bash"
    assert result[0]["content"][0]["input"] == {"command": "ls"}


def test_convert_assistant_tool_calls_invalid_json() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "not-json"},
                }
            ],
        }
    ]
    result, _ = _convert_openai_messages_to_anthropic(messages)
    assert result[0]["content"][0]["input"] == {}


def test_convert_tool_result() -> None:
    messages = [{"role": "tool", "tool_call_id": "call_1", "content": "output text"}]
    result, _ = _convert_openai_messages_to_anthropic(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["type"] == "tool_result"
    assert result[0]["content"][0]["tool_use_id"] == "call_1"
    assert result[0]["content"][0]["content"] == "output text"


def test_convert_tool_result_empty_content() -> None:
    messages = [{"role": "tool", "tool_call_id": "call_1", "content": ""}]
    result, _ = _convert_openai_messages_to_anthropic(messages)
    assert result[0]["content"][0]["content"] == ""


def test_convert_mixed_messages() -> None:
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Bye"},
    ]
    result, _system = _convert_openai_messages_to_anthropic(messages)
    assert _system == ["System prompt"]
    assert len(result) == 3
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
    assert result[2]["role"] == "user"


def test_convert_tools_empty_list() -> None:
    result = _convert_openai_tools_to_anthropic([])
    assert result == []


def test_convert_tools_single() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            },
        }
    ]
    result = _convert_openai_tools_to_anthropic(tools)
    assert len(result) == 1
    assert result[0]["name"] == "bash"
    assert result[0]["description"] == "Run a command"
    assert result[0]["input_schema"]["type"] == "object"


def test_convert_tools_missing_function_field() -> None:
    tools = [{"type": "function"}]
    result = _convert_openai_tools_to_anthropic(tools)
    assert len(result) == 1
    assert result[0]["name"] == ""
    assert result[0]["input_schema"] == {"type": "object", "properties": {}, "required": []}


# --- Stream conversion edge case tests ---

from psi_agent.ai.anthropic_messages.server import _convert_anthropic_stream_to_openai_sse  # noqa: E402


class _FakeStreamResponse:
    """Minimal fake aiohttp response for testing SSE conversion."""
    def __init__(self):
        self._written: list[str] = []

    async def write(self, data: bytes) -> None:
        self._written.append(data.decode())


class _FakeByteStream:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx] + "\n"
        self._idx += 1
        return line.encode()


@pytest.mark.anyio
async def test_anthropic_sse_json_decode_error() -> None:
    """JSON decode error in SSE data should be skipped gracefully."""
    resp = _FakeStreamResponse()
    stream = _FakeByteStream(["data: not-json-at-all", "event: message_stop", "data: {}"])
    await _convert_anthropic_stream_to_openai_sse(resp, stream)
    all_written = "".join(resp._written)
    assert "finish_reason" in all_written


@pytest.mark.anyio
async def test_anthropic_sse_unknown_event_type() -> None:
    """Unknown event type should be skipped."""
    resp = _FakeStreamResponse()
    stream = _FakeByteStream(["data: {\"type\": \"unknown_xyz\", \"index\": 0}", "event: message_stop", "data: {}"])
    await _convert_anthropic_stream_to_openai_sse(resp, stream)
    all_written = "".join(resp._written)
    assert "finish_reason" in all_written


@pytest.mark.anyio
async def test_anthropic_sse_non_data_non_event_line() -> None:
    """Line not starting with data: or event: should still be parsed."""
    resp = _FakeStreamResponse()
    stream = _FakeByteStream(["{\"type\": \"message_stop\"}", "data: {}"])
    await _convert_anthropic_stream_to_openai_sse(resp, stream)
    all_written = "".join(resp._written)
    assert "finish_reason" in all_written


@pytest.mark.anyio
async def test_anthropic_sse_message_delta_event() -> None:
    """message_delta event type should be handled without crashing."""
    resp = _FakeStreamResponse()
    stream = _FakeByteStream([
        "data: {\"type\": \"message_delta\", \"delta\": {\"stop_reason\": \"end_turn\"}}",
        "event: message_stop",
        "data: {}",
    ])
    await _convert_anthropic_stream_to_openai_sse(resp, stream)
    all_written = "".join(resp._written)
    assert "finish_reason" in all_written


@pytest.mark.anyio
async def test_anthropic_sse_input_json_for_unknown_index() -> None:
    """input_json_delta for an index not in current_tool_calls should be handled."""
    resp = _FakeStreamResponse()
    stream = _FakeByteStream([
        "data: {\"type\": \"content_block_delta\", \"index\": 99, "
        "\"delta\": {\"type\": \"input_json_delta\", \"partial_json\": \"{\\\"x\\\": 1}\"}}",
        "event: message_stop",
        "data: {}",
    ])
    await _convert_anthropic_stream_to_openai_sse(resp, stream)
    all_written = "".join(resp._written)
    assert "finish_reason" in all_written
