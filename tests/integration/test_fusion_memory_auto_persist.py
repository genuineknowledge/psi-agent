from __future__ import annotations

import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import web

from tests.integration.conftest import _psi_process_spec, read_sse
from tests.integration.test_end_to_end import _chunk, _stop_process, _wait_for_socket


async def _start_memory_server(status: int, seen: list[dict]) -> tuple[web.AppRunner, int]:
    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"span_ids": ["span-1"]}, status=status)

    app = web.Application()
    app.router.add_post("/ingest-turn", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return runner, port


async def _wait_for_ingest(seen: list[dict], count: int = 1, *, timeout_sec: float = 5.0) -> dict:
    deadline = anyio.current_time() + timeout_sec
    while anyio.current_time() < deadline:
        if len(seen) >= count:
            return seen[count - 1]
        await anyio.sleep(0.05)
    pytest.fail(f"Fusion Memory ingest count {count} was not observed within {timeout_sec}s")


async def _start_scripted_ai_server(response_chunks_by_request: list[list[str]]) -> tuple[web.AppRunner, str]:
    requests_seen = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal requests_seen
        requests_seen += 1
        assert requests_seen <= len(response_chunks_by_request), "unexpected extra AI request"
        await request.json()
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for line in response_chunks_by_request[requests_seen - 1]:
            if line == "[DONE]":
                await resp.write(b"data: [DONE]\n\n")
            else:
                await resp.write(f"data: {line}\n\n".encode())
        if response_chunks_by_request[requests_seen - 1][-1] != "[DONE]":
            await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.anyio
async def test_turn_auto_persist_posts_new_messages_without_blocking(
    tmp_path: Path,
) -> None:
    seen: list[dict] = []
    memory_runner, memory_port = await _start_memory_server(200, seen)
    ai_runner, base_url = await _start_scripted_ai_server(
        [
            [_chunk(content="first answer", finish_reason="stop")],
            [
                _chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command":"printf second-turn-tool"}'},
                        }
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [_chunk(content="done after tool", finish_reason="stop")],
        ]
    )

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_cmd, ai_env, ai_cwd = _psi_process_spec(
        "ai",
        "--provider",
        "openai",
        "--session-socket",
        ai_socket,
        "--model",
        "test",
        "--api-key",
        "k",
        "--base-url",
        base_url,
    )
    ai_proc = await anyio.open_process(ai_cmd, env=ai_env, cwd=str(ai_cwd))

    ses_cmd, ses_env, ses_cwd = _psi_process_spec(
        "session",
        "--workspace",
        "examples/a-simple-schedule-workspace",
        "--channel-socket",
        channel_socket,
        "--ai-socket",
        ai_socket,
    )
    ses_env = dict(ses_env)
    ses_env.update(
        {
            "PSI_MEMORY_BASE_URL": f"http://127.0.0.1:{memory_port}",
            "PSI_MEMORY_WORKSPACE_ID": "ws",
            "PSI_MEMORY_USER_ID": "u",
            "PSI_MEMORY_AGENT_ID": "dolphin",
            "PSI_MEMORY_SESSION_ID": "session-1",
        }
    )
    ses_proc = await anyio.open_process(ses_cmd, env=ses_env, cwd=str(ses_cwd))

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)
        first_chunks = await read_sse(channel_socket, "first turn")
        assert first_chunks
        first_payload = await _wait_for_ingest(seen, 1)
        assert [message["role"] for message in first_payload["messages"]] == ["user", "assistant"]
        assert first_payload["messages"][0]["content"] == "first turn"
        assert first_payload["messages"][1]["content"] == "first answer"
        assert first_payload["metadata"]["ended_with_error"] is False

        second_chunks = await read_sse(channel_socket, "please use bash tool")
        assert second_chunks
        second_payload = await _wait_for_ingest(seen, 2)
        assert [message["role"] for message in second_payload["messages"]] == ["user", "assistant", "tool", "assistant"]
        assert second_payload["messages"][0]["content"] == "please use bash tool"
        assert second_payload["messages"][1]["tool_calls"][0]["function"]["name"] == "bash"
        assert second_payload["messages"][2]["name"] == "bash"
        assert second_payload["messages"][2]["content"] == "second-turn-tool"
        assert second_payload["messages"][3]["content"] == "done after tool"
        assert all(message.get("content") != "first turn" for message in second_payload["messages"])
        assert all(message.get("content") != "first answer" for message in second_payload["messages"])
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await ai_runner.cleanup()
        await memory_runner.cleanup()


@pytest.mark.anyio
async def test_turn_auto_persist_failure_does_not_fail_session(
    tmp_path: Path,
    mock_ai_server,
) -> None:
    seen: list[dict] = []
    memory_runner, memory_port = await _start_memory_server(500, seen)
    mock_ai_server.set_responses([_chunk(content="still answered", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_cmd, ai_env, ai_cwd = _psi_process_spec(
        "ai",
        "--provider",
        "openai",
        "--session-socket",
        ai_socket,
        "--model",
        "test",
        "--api-key",
        "k",
        "--base-url",
        base_url,
    )
    ai_proc = await anyio.open_process(ai_cmd, env=ai_env, cwd=str(ai_cwd))

    ses_cmd, ses_env, ses_cwd = _psi_process_spec(
        "session",
        "--workspace",
        "examples/a-simple-schedule-workspace",
        "--channel-socket",
        channel_socket,
        "--ai-socket",
        ai_socket,
    )
    ses_env = dict(ses_env)
    ses_env.update(
        {
            "PSI_MEMORY_BASE_URL": f"http://127.0.0.1:{memory_port}",
            "PSI_MEMORY_WORKSPACE_ID": "ws",
            "PSI_MEMORY_USER_ID": "u",
            "PSI_MEMORY_AGENT_ID": "dolphin",
            "PSI_MEMORY_SESSION_ID": "session-1",
        }
    )
    ses_proc = await anyio.open_process(ses_cmd, env=ses_env, cwd=str(ses_cwd))

    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)
        first_chunks = await read_sse(channel_socket, "hello")
        first_content = "".join(
            chunk.get("choices", [{}])[0].get("delta", {}).get("content", "") for chunk in first_chunks
        )
        assert "still answered" in first_content
        first_payload = await _wait_for_ingest(seen, 1)
        assert first_payload["messages"][0]["content"] == "hello"
        assert first_payload["metadata"]["ended_with_error"] is False

        second_chunks = await read_sse(channel_socket, "hello again")
        second_content = "".join(
            chunk.get("choices", [{}])[0].get("delta", {}).get("content", "") for chunk in second_chunks
        )
        assert "still answered" in second_content
        second_payload = await _wait_for_ingest(seen, 2)
        assert second_payload["messages"][0]["content"] == "hello again"
        assert second_payload["metadata"]["ended_with_error"] is False
    finally:
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
        await memory_runner.cleanup()
