from __future__ import annotations

import json
import signal
import socket
import subprocess
import textwrap
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

# --- Workspace fixture ---


@pytest.fixture
async def temp_workspace(tmp_path: Path) -> Path:
    """Create a minimal temporary workspace with tool, system, and schedule."""
    ws = tmp_path / "workspace"

    tools_dir = ws / "tools"
    await anyio.Path(tools_dir).mkdir(parents=True)
    await anyio.Path(tools_dir / "echo.py").write_text(
        textwrap.dedent("""\
        async def echo(message: str) -> str:
            \"\"\"Echo back the message.

            Args:
                message: The message to echo.
            \"\"\"
            return f"ECHO: {message}"
    """)
    )

    systems_dir = ws / "systems"
    await anyio.Path(systems_dir).mkdir(parents=True)
    await anyio.Path(systems_dir / "system.py").write_text(
        textwrap.dedent("""\
        async def system_prompt_builder() -> str:
            return "You are a helpful test assistant."
    """)
    )

    schedules_dir = ws / "schedules" / "test-sched"
    await anyio.Path(schedules_dir).mkdir(parents=True)
    await anyio.Path(schedules_dir / "TASK.md").write_text('---\nname: test-sched\ncron: "0 0 1 1 *"\n---\nTest task.')

    return ws


# --- Mock AI server fixture ---


class MockAIServer:
    """Configurable mock AI server that returns predefined SSE response chunks."""

    def __init__(self, tmp_path: Path) -> None:
        self._runner: web.AppRunner | None = None
        self.port: int = 0
        self.base_url: str = ""
        self.request_bodies: list[dict] = []
        self.response_chunks: list[str] = []

    def set_responses(self, chunks: list[str]) -> None:
        """Set SSE data: lines to return (without 'data: ' prefix or \n\n suffix)."""
        self.response_chunks = list(chunks)

    async def start(self) -> str:
        """Start the mock server and return the base URL."""
        app = web.Application()
        self.request_bodies = []

        response_chunks = self.response_chunks

        async def handler(request: web.Request) -> web.StreamResponse:
            body = await request.json()
            self.request_bodies.append(body)
            resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for line in response_chunks:
                if line == "[DONE]":
                    await resp.write(b"data: [DONE]\n\n")
                else:
                    await resp.write(f"data: {line}\n\n".encode())
            if response_chunks and response_chunks[-1] != "[DONE]":
                await resp.write(b"data: [DONE]\n\n")
            return resp

        app.router.add_post("/chat/completions", handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        site = web.SockSite(self._runner, sock)
        await site.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        return self.base_url

    async def cleanup(self) -> None:
        if self._runner:
            await self._runner.cleanup()


@pytest.fixture
async def mock_ai_server(tmp_path: Path) -> MockAIServer:  # ty: ignore[invalid-return-type]
    srv = MockAIServer(tmp_path)
    yield srv
    await srv.cleanup()


# --- SSE reader utility ---


async def read_sse(socket_path: str, message: str = "hello", *, timeout_sec: float = 10.0) -> list[dict]:
    """Connect to a Unix socket, send a chat message, collect all SSE data chunks."""
    chunks: list[dict] = []
    body = {"model": "test", "messages": [{"role": "user", "content": message}], "stream": True}
    timeout = ClientTimeout(total=timeout_sec)
    connector = UnixConnector(path=socket_path)
    async with (
        ClientSession(connector=connector, timeout=timeout) as session,
        session.post("http://localhost/chat/completions", json=body) as resp,
    ):
        async for raw in resp.content:
            line = raw.decode().strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunks.append(json.loads(data_str))
            except json.JSONDecodeError:
                continue
    return chunks


def read_sse_sync(socket_path: str, message: str = "hello", *, timeout_sec: float = 10.0) -> list[dict]:
    """Synchronous wrapper for read_sse."""
    return anyio.run(lambda: read_sse(socket_path, message, timeout_sec=timeout_sec))


# --- Subprocess helpers ---


def _start_psi(*args: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["uv", "run", "psi-agent", *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _wait_for_socket(sock_path: Path, timeout_sec: float = 10.0) -> None:
    deadline = anyio.current_time() + timeout_sec
    sock_anyio = anyio.Path(str(sock_path))
    while anyio.current_time() < deadline:
        if await sock_anyio.exists():
            await anyio.sleep(0.3)
            return
        await anyio.sleep(0.1)
    pytest.fail(f"Socket {sock_path} not created within {timeout_sec}s")


def _kill(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
