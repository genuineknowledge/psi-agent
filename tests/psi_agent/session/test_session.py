from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.session import _load_after_turn_fn, _load_system_prompt_builder


def test_system_py_not_exists(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # No systems/ directory at all
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_py_missing_system_prompt_builder(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def unrelated():\n    pass")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_prompt_builder_not_async(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def system_prompt_builder():\n    return 'hello'")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_prompt_builder_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("async def system_prompt_builder() -> str:\n    return 'test prompt'")
    builder = _load_system_prompt_builder(ws)
    assert builder is not None

    result = asyncio.run(builder())
    assert result == "test prompt"


def test_system_class_build_system_prompt_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text(
        """
class System:
    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir

    async def build_system_prompt(self, model=None, tool_names=None):
        return f"{model}:{','.join(tool_names or [])}:{self.workspace_dir.name}"
""".strip()
    )

    builder = _load_system_prompt_builder(ws, model="test-model", tool_names=["bash", "read"])
    assert builder is not None

    result = asyncio.run(builder())
    assert result == "test-model:bash,read:ws"


def test_syntax_error_in_system_py(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("this is not valid python {{{")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_after_turn_returns_none_when_missing(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text(
        """
class System:
    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir
""".strip()
    )

    result = _load_after_turn_fn(ws, ai_socket="http://127.0.0.1:1/v1", model="test", tool_executors={})

    assert result is None


@pytest.mark.anyio
async def test_after_turn_loads_and_injects_helpers(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text(
        """
import json


class System:
    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir

    async def after_turn(self, messages, tool_call_count, called_tools, *, complete_fn=None, tool_executors=None):
        tool_result = await tool_executors["echo"](message="hello")
        response = await complete_fn([{"role": "user", "content": "summarize"}], [])
        payload = {
            "messages": len(messages),
            "tool_call_count": tool_call_count,
            "called_tools": called_tools,
            "tool_result": tool_result,
            "completion": response["choices"][0]["message"]["content"],
            "finish_reason": response["choices"][0]["finish_reason"],
        }
        await (self.workspace_dir / "after_turn.json").write_text(json.dumps(payload), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    ai_requests: list[dict] = []

    async def ai_handler(request: web.Request) -> web.StreamResponse:
        ai_requests.append(await request.json())
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "summary"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def echo(message: str) -> str:
        return f"ECHO: {message}"

    app = web.Application()
    app.router.add_post("/v1/chat/completions", ai_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    try:
        after_turn = _load_after_turn_fn(
            ws,
            ai_socket=f"http://127.0.0.1:{port}/v1",
            model="test-model",
            tool_executors={"echo": echo},
        )
        assert after_turn is not None

        await after_turn(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
            1,
            ["echo"],
        )

        payload = json.loads((ws / "after_turn.json").read_text(encoding="utf-8"))
        assert payload == {
            "messages": 2,
            "tool_call_count": 1,
            "called_tools": ["echo"],
            "tool_result": "ECHO: hello",
            "completion": "summary",
            "finish_reason": "stop",
        }
        assert ai_requests[0]["model"] == "test-model"
        assert ai_requests[0]["stream"] is True
    finally:
        await runner.cleanup()
