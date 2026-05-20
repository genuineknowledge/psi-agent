from __future__ import annotations

"""Integration tests using real LLM APIs. Set env vars to enable each test group."""

import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import anyio  # noqa: E402
import pytest  # noqa: E402
from aiohttp import ClientSession, ClientTimeout, UnixConnector  # noqa: E402


def _require_env(*names: str) -> tuple[str, ...]:
    """Return env var values or skip the test."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return tuple(os.environ[n] for n in names)


async def _read_sse_stream(connector: UnixConnector, socket_path: str, model: str | None = None) -> list[str]:
    chunks: list[str] = []
    body: dict = {
        "messages": [{"role": "user", "content": "Say exactly 'hello world'"}],
        "stream": True,
    }
    if model is not None:
        body["model"] = model

    timeout = ClientTimeout(total=60)
    async with (
        ClientSession(connector=connector, timeout=timeout) as session,
        session.post("http://localhost/v1/chat/completions", json=body) as resp,
    ):
        assert resp.status == 200, f"Got status {resp.status}: {await resp.text()}"
        async for raw in resp.content:
            chunk = raw.decode().strip()
            if chunk.startswith("data: ") and chunk != "data: [DONE]":
                chunks.append(chunk)
    return chunks


def _start_psi(*args: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["uv", "run", "psi-agent", *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _wait_for_socket(sock_path: Path, timeout_sec: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_sec
    sock_anyio = anyio.Path(str(sock_path))
    while time.monotonic() < deadline:
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


# --- OpenAI tests ---


@pytest.mark.anyio
async def test_openai_layer_real_api(tmp_path: Path) -> None:
    api_key, base_url, model = _require_env(
        "PSI_TEST_OPENAI_API_KEY", "PSI_TEST_OPENAI_BASE_URL", "PSI_TEST_OPENAI_MODEL"
    )
    socket_path = tmp_path / "ai.sock"

    proc = _start_psi(
        "ai",
        "openai-completions",
        "--session-socket",
        str(socket_path),
        "--model",
        model,
        "--api-key",
        api_key,
        "--base-url",
        base_url,
    )

    try:
        await _wait_for_socket(socket_path)
        connector = UnixConnector(path=str(socket_path))
        chunks = await _read_sse_stream(connector, str(socket_path), model=model)

        assert len(chunks) > 0, "No chunks received"
        all_text = "".join(chunks)
        assert "hello" in all_text.lower(), f"Expected 'hello' in response, got: {all_text[:500]}"
        print(f"\n  ✓ openai-completions: {len(chunks)} chunks")

    finally:
        _kill(proc)
        if socket_path.exists():
            socket_path.unlink()


@pytest.mark.anyio
async def test_openai_layer_ignores_client_model(tmp_path: Path) -> None:
    api_key, base_url, model = _require_env(
        "PSI_TEST_OPENAI_API_KEY", "PSI_TEST_OPENAI_BASE_URL", "PSI_TEST_OPENAI_MODEL"
    )
    socket_path = tmp_path / "ai_override.sock"

    proc = _start_psi(
        "ai",
        "openai-completions",
        "--session-socket",
        str(socket_path),
        "--model",
        model,
        "--api-key",
        api_key,
        "--base-url",
        base_url,
    )

    try:
        await _wait_for_socket(socket_path)
        connector = UnixConnector(path=str(socket_path))
        chunks = await _read_sse_stream(connector, str(socket_path), model="garbage-nonexistent-model")

        assert len(chunks) > 0, "No chunks received with garbage model"
        all_text = "".join(chunks)
        assert "hello" in all_text.lower(), f"Expected 'hello', got: {all_text[:500]}"
        print("\n  ✓ model override: AI server ignored garbage model, still works")

    finally:
        _kill(proc)
        if socket_path.exists():
            socket_path.unlink()


@pytest.mark.anyio
async def test_session_end_to_end(tmp_path: Path) -> None:
    api_key, base_url, model = _require_env(
        "PSI_TEST_OPENAI_API_KEY", "PSI_TEST_OPENAI_BASE_URL", "PSI_TEST_OPENAI_MODEL"
    )
    ai_socket = tmp_path / "ai.sock"
    channel_socket = tmp_path / "channel.sock"

    ai_proc = _start_psi(
        "ai",
        "openai-completions",
        "--session-socket",
        str(ai_socket),
        "--model",
        model,
        "--api-key",
        api_key,
        "--base-url",
        base_url,
    )

    session_proc = _start_psi(
        "session",
        "--workspace",
        "examples/a-simple-bash-only-workspace",
        "--channel-socket",
        str(channel_socket),
        "--ai-socket",
        str(ai_socket),
        "--model",
        model,
    )

    try:
        for sock in [ai_socket, channel_socket]:
            await _wait_for_socket(sock, timeout_sec=15.0)

        connector = UnixConnector(path=str(channel_socket))
        chunks = await _read_sse_stream(connector, str(channel_socket), model=model)

        all_text = "".join(chunks)
        assert len(chunks) > 0, f"No response from session. Text: {all_text[:500]}"
        print(f"\n  ✓ session e2e: {len(chunks)} chunks")

    finally:
        for proc in [session_proc, ai_proc]:
            _kill(proc)
        for p in [ai_socket, channel_socket]:
            if p.exists():
                p.unlink()


# --- Anthropic tests ---


@pytest.mark.anyio
async def test_anthropic_layer_real_api(tmp_path: Path) -> None:
    api_key, base_url, model = _require_env(
        "PSI_TEST_ANTHROPIC_API_KEY", "PSI_TEST_ANTHROPIC_BASE_URL", "PSI_TEST_ANTHROPIC_MODEL"
    )
    socket_path = tmp_path / "ai_anthro.sock"

    proc = _start_psi(
        "ai",
        "anthropic-messages",
        "--session-socket",
        str(socket_path),
        "--model",
        model,
        "--api-key",
        api_key,
        "--base-url",
        base_url,
    )

    try:
        await _wait_for_socket(socket_path)
        connector = UnixConnector(path=str(socket_path))
        chunks = await _read_sse_stream(connector, str(socket_path), model=model)

        assert len(chunks) > 0, "No chunks received from Anthropic backend"
        all_text = "".join(chunks)
        assert "hello" in all_text.lower(), f"Expected 'hello', got: {all_text[:500]}"
        print(f"\n  ✓ anthropic-messages: {len(chunks)} chunks")

    finally:
        _kill(proc)
        if socket_path.exists():
            socket_path.unlink()
