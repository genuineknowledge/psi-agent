from __future__ import annotations

import contextlib
import json

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from tests.integration.conftest import MockAIServer


def _chunk(content: str = "", reasoning: str = "", finish_reason: str | None = None) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    if reasoning:
        d["reasoning_content"] = reasoning
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


async def _wait_for_socket(sock_path: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    sock_anyio = anyio.Path(sock_path)
    while anyio.current_time() < deadline:
        if await sock_anyio.exists():
            await anyio.sleep(0.3)
            return True
        await anyio.sleep(0.1)
    return False


async def _wait_for_http(base_url: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    timeout = ClientTimeout(total=2)
    while anyio.current_time() < deadline:
        try:
            async with ClientSession(timeout=timeout) as session, session.get(base_url) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        await anyio.sleep(0.2)
    return False


async def _stop_process(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except Exception:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _read_web_chat(base_url: str, message: str = "hello") -> tuple[str, str]:
    """Send a message to the web channel /api/chat and collect content + reasoning."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    timeout = ClientTimeout(total=15)
    async with (
        ClientSession(timeout=timeout) as session,
        session.post(base_url.rstrip("/") + "/api/chat", json={"message": message}) as resp,
    ):
        assert resp.status == 200, f"Got {resp.status}"
        async for raw in resp.content:
            line = raw.decode().strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            evt = json.loads(data_str)
            if evt.get("content"):
                content_parts.append(evt["content"])
            if evt.get("reasoning"):
                reasoning_parts.append(evt["reasoning"])
    return "".join(content_parts), "".join(reasoning_parts)


@pytest.mark.anyio
async def test_web_channel_streams_session_reply(tmp_path, mock_ai_server: MockAIServer) -> None:
    """Web channel should serve the page and stream session content over /api/chat."""
    mock_ai_server.set_responses([_chunk(reasoning="thinking"), _chunk(content="Hello from web", finish_reason="stop")])
    base_url_ai = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")
    web_listen = "http://127.0.0.1:8799"

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "openai-completions",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url_ai,
        ],
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ],
    )

    web_proc = None
    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)

        web_proc = await anyio.open_process(
            [
                "uv",
                "run",
                "psi-agent",
                "channel",
                "web",
                "--session-socket",
                channel_socket,
                "--listen",
                web_listen,
            ],
        )
        assert await _wait_for_http(web_listen)

        content, reasoning = await _read_web_chat(web_listen, "hi")
        assert "Hello from web" in content, f"Got content: {content[:200]}"
        assert "thinking" in reasoning, f"Got reasoning: {reasoning[:200]}"
    finally:
        if web_proc is not None:
            await _stop_process(web_proc)
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)
