# ruff: noqa: E402, E501, ASYNC220, ASYNC221, ASYNC240, ASYNC251, SIM117, F841, F401
from __future__ import annotations

"""Full end-to-end integration tests with mock AI."""

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests.integration.conftest import MockAIServer


def _chunk(
    content: str = "", reasoning: str = "", tool_calls: list | None = None, finish_reason: str | None = None
) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    if reasoning:
        d["reasoning_content"] = reasoning
    if tool_calls:
        d["tool_calls"] = tool_calls
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


@pytest.mark.anyio
async def test_full_pipeline_mock_ai(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Full pipeline: mock AI → session → CLI channel."""
    mock_ai_server.set_responses([_chunk(content="pipeline works", finish_reason="stop")])
    base_url = await mock_ai_server.start()

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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        result = subprocess.run(
            [
                "uv",
                "run",
                "psi-agent",
                "channel",
                "cli",
                "--session-socket",
                str(channel_socket),
                "--message",
                "test pipeline",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "pipeline works" in result.stdout, f"stdout: {result.stdout}, stderr: {result.stderr}"
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.mark.anyio
async def test_full_pipeline_with_tool(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Full pipeline with tool call: mock AI → tool execution → final response."""
    mock_ai_server.set_responses(
        [
            _chunk(reasoning="Let me use the tool", finish_reason=None),
            _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"message":"tool test"}'},
                    }
                ],
                finish_reason="tool_calls",
            ),
            _chunk(content="Final: tool was called", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        result = subprocess.run(
            [
                "uv",
                "run",
                "psi-agent",
                "channel",
                "cli",
                "--session-socket",
                str(channel_socket),
                "--message",
                "use tool",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout + result.stderr
        assert "Final: tool was called" in out, f"output: {out[:500]}"
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.mark.anyio
async def test_multiple_messages_history_accumulates(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Two channel messages should cause history accumulation in session."""
    mock_ai_server.set_responses(
        [
            _chunk(content="response 1", finish_reason="stop"),
            _chunk(content="response 2", finish_reason="stop"),
        ]
    )
    base_url = await mock_ai_server.start()

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
    ses_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            str(ai_socket),
            "--model",
            "test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        for s in [ai_socket, channel_socket]:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if s.exists():
                    time.sleep(0.3)
                    break
                time.sleep(0.1)

        r1 = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "msg1"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        time.sleep(0.3)
        r2 = subprocess.run(
            ["uv", "run", "psi-agent", "channel", "cli", "--session-socket", str(channel_socket), "--message", "msg2"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "response 1" in r1.stdout
        assert "response 2" in r2.stdout

        # Verify second request has more history
        assert len(mock_ai_server.request_bodies) == 2
        assert len(mock_ai_server.request_bodies[1]["messages"]) > len(mock_ai_server.request_bodies[0]["messages"])
    finally:
        for p in [ses_proc, ai_proc]:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
