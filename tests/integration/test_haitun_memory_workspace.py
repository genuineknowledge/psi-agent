from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.tool_registry import ToolRegistry

WORKSPACE = Path("examples/haitun-workspace")


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _clear_memory_helper_modules() -> None:
    for name in ("_client", "_config"):
        sys.modules.pop(name, None)


class _RecordingMemoryServer:
    def __init__(self) -> None:
        self.request_bodies: list[dict] = []
        self.paths: list[str] = []
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> str:
        async def add_handler(request: web.Request) -> web.Response:
            body = await request.json()
            self.paths.append(request.path)
            self.request_bodies.append(body)
            return web.json_response({"id": "memory-1", "stored": True})

        async def search_handler(request: web.Request) -> web.Response:
            body = await request.json()
            self.paths.append(request.path)
            self.request_bodies.append(body)
            return web.json_response({"candidates": [{"text": "Haitun remembers existing context"}]})

        app = web.Application()
        app.router.add_post("/add", add_handler)
        app.router.add_post("/search", search_handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        port = _unused_tcp_port()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    async def cleanup(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


class _RecordingAIServer:
    def __init__(self) -> None:
        self.request_bodies: list[dict] = []
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> str:
        async def handler(request: web.Request) -> web.StreamResponse:
            body = await request.json()
            self.request_bodies.append(body)
            resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(
                (
                    "data: "
                    + json.dumps(
                        {
                            "id": "test",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "test",
                            "choices": [{"index": 0, "delta": {"content": "agent started"}, "finish_reason": "stop"}],
                        }
                    )
                    + "\n\n"
                ).encode()
            )
            await resp.write(b"data: [DONE]\n\n")
            return resp

        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        port = _unused_tcp_port()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    async def cleanup(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


async def _wait_for_socket(socket_path: Path, timeout_seconds: float = 10.0) -> None:
    deadline = anyio.current_time() + timeout_seconds
    socket_anyio = anyio.Path(str(socket_path))
    while anyio.current_time() < deadline:
        if await socket_anyio.exists():
            await anyio.sleep(0.3)
            return
        await anyio.sleep(0.1)
    pytest.fail(f"Socket was not created: {socket_path}")


async def _stop_process(process) -> None:
    process.terminate()
    try:
        with anyio.fail_after(5):
            await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()


def test_haitun_workspace_contains_memory_files() -> None:
    assert (WORKSPACE / "skills" / "fusion-memory-setup" / "SKILL.md").is_file()
    assert (WORKSPACE / "tools" / "_client.py").is_file()
    assert (WORKSPACE / "tools" / "_config.py").is_file()
    assert (WORKSPACE / "tools" / "memory_add.py").is_file()
    assert (WORKSPACE / "tools" / "memory_search.py").is_file()
    assert (WORKSPACE / "tools" / "memory_answer_context.py").is_file()


@pytest.mark.anyio
async def test_haitun_prompt_exposes_memory_guidance() -> None:
    agent = await SessionAgent.create(
        ai_socket="http://127.0.0.1:9",
        workspace_path=WORKSPACE,
        session_id="haitun-memory-prompt-test",
    )
    await agent._system_prompt.ensure(agent._conversation)
    system_prompt = agent._conversation.messages[0]["content"]

    assert "Fusion Memory" in system_prompt
    assert "memory_add" in system_prompt
    assert "memory_search" in system_prompt
    assert "memory_answer_context" in system_prompt
    assert "Before the first use of Fusion Memory" in system_prompt
    assert "fusion-memory-setup" in system_prompt


@pytest.mark.anyio
async def test_haitun_memory_tools_call_fusion_memory_service(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _RecordingMemoryServer()
    base_url = await server.start()
    monkeypatch.setenv("PSI_MEMORY_BASE_URL", base_url)
    monkeypatch.setenv("PSI_MEMORY_WORKSPACE_ID", "haitun-test")
    monkeypatch.setenv("PSI_MEMORY_USER_ID", "workspace-user")
    monkeypatch.setenv("PSI_MEMORY_AGENT_ID", "haitun")
    monkeypatch.delenv("PSI_MEMORY_SESSION_ID", raising=False)
    _clear_memory_helper_modules()

    try:
        registry = await ToolRegistry.load(WORKSPACE / "tools", "haitun-memory-service-test")
        memory_add = registry.get("memory_add")
        memory_search = registry.get("memory_search")
        assert memory_add is not None
        assert memory_search is not None

        add_result = json.loads(await memory_add("Haitun should persist durable facts", source="integration-test"))
        search_result = await memory_search("Haitun context", limit=2)

        assert add_result == {"ok": True, "saved": True, "result": {"id": "memory-1", "stored": True}}
        assert search_result == "Fusion Memory context:\n- Haitun remembers existing context"
        assert server.paths == ["/add", "/search"]
        assert server.request_bodies[0]["scope"] == {
            "workspace_id": "haitun-test",
            "user_id": "workspace-user",
            "agent_id": "haitun",
            "session_id": None,
            "app_id": "haitun",
        }
        assert server.request_bodies[1]["options"] == {"limit": 2, "allow_cross_session": True}
    finally:
        _clear_memory_helper_modules()
        await server.cleanup()


@pytest.mark.anyio
async def test_isolated_haitun_session_start_does_not_run_memory_install(tmp_path: Path) -> None:
    isolated_workspace = tmp_path / "haitun-workspace"
    shutil.copytree(WORKSPACE, isolated_workspace)
    fake_memory = tmp_path / "fusion-memory"
    model_dir = fake_memory / "models" / "Qwen3-Embedding-0.6B"
    reranker_dir = fake_memory / "models" / "Qwen3-Reranker-0.6B"
    model_dir.mkdir(parents=True)
    reranker_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (reranker_dir / "config.json").write_text("{}")
    install_counter = fake_memory / "install-count.txt"
    (fake_memory / "install.sh").write_text(
        "#!/bin/sh\ncount=0\n[ -f install-count.txt ] && count=$(cat install-count.txt)\n"
        "count=$((count + 1))\nprintf '%s' \"$count\" > install-count.txt\n"
    )
    (fake_memory / "install.sh").chmod(0o755)

    server = _RecordingAIServer()
    base_url = await server.start()
    channel_socket = tmp_path / "haitun-channel.sock"
    env = os.environ.copy()
    env["FUSION_MEMORY_HOME"] = str(fake_memory)
    env["PATH"] = f"{fake_memory}:{env['PATH']}"

    process = await anyio.open_process(
        [
            sys.executable,
            "-m",
            "psi_agent.cli",
            "session",
            "--workspace",
            str(isolated_workspace),
            "--channel-socket",
            str(channel_socket),
            "--ai-socket",
            base_url,
            "--session-id",
            "haitun-isolated-memory-start",
        ],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await _wait_for_socket(channel_socket)
        assert len(server.request_bodies) == 0
        assert not install_counter.exists()
        assert (model_dir / "config.json").read_text() == "{}"
        assert (reranker_dir / "config.json").read_text() == "{}"
    finally:
        await _stop_process(process)
        await server.cleanup()
