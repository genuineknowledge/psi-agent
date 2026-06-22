from __future__ import annotations

import json
import socket as _s
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice
from psi_agent.session.server import handle_chat_completions


class _FailingSessionAgent(SessionAgent):
    """SessionAgent that raises an exception mid-stream."""

    async def run(self, user_message: dict):
        yield ChatCompletionChunk(choices=[StreamChoice(delta=DeltaMessage(content="partial"))])
        raise RuntimeError("boom")


@pytest.mark.anyio
async def test_handle_invalid_json_body(tmp_path: Path) -> None:
    """When request body is not valid JSON, return 400."""
    app = web.Application()
    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={}, model="test")
    lock = anyio.Lock()
    app["agent"] = agent
    app["lock"] = lock
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()

    try:
        await anyio.sleep(0.1)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post("http://localhost/v1/chat/completions", data="not json") as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_handle_empty_messages(tmp_path: Path) -> None:
    """When messages list is empty, return 400."""
    app = web.Application()
    agent = SessionAgent(ai_socket="http://nonexistent/v1", tools={}, model="test")
    lock = anyio.Lock()
    app["agent"] = agent
    app["lock"] = lock
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()

    try:
        await anyio.sleep(0.1)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/v1/chat/completions",
                json={"model": "test", "messages": [], "stream": True},
            ) as resp,
        ):
            assert resp.status == 400
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_handle_non_user_role_coercion(tmp_path: Path) -> None:
    """When last message is not user role, it should work without crashing."""

    async def ai_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    ai_app = web.Application()
    ai_app.router.add_post("/v1/chat/completions", ai_handler)
    ai_runner = web.AppRunner(ai_app)
    await ai_runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    ai_site = web.SockSite(ai_runner, sock)
    await ai_site.start()

    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
        lock = anyio.Lock()

        app = web.Application()
        app["agent"] = agent
        app["lock"] = lock
        app.router.add_post("/v1/chat/completions", handle_chat_completions)

        runner = web.AppRunner(app)
        await runner.setup()
        socket_path = str(tmp_path / "s.sock")
        site = web.UnixSite(runner, socket_path)
        await site.start()

        try:
            await anyio.sleep(0.1)
            connector = UnixConnector(path=socket_path)
            timeout = ClientTimeout(total=5)
            async with (
                ClientSession(connector=connector, timeout=timeout) as s,
                s.post(
                    "http://localhost/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "assistant", "content": "ignored"}], "stream": True},
                ) as resp,
            ):
                assert resp.status == 200
        finally:
            await runner.cleanup()
    finally:
        await ai_runner.cleanup()


@pytest.mark.anyio
async def test_agent_run_success_flow(tmp_path: Path) -> None:
    """When agent runs successfully, response is streamed correctly."""

    async def ai_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    ai_app = web.Application()
    ai_app.router.add_post("/v1/chat/completions", ai_handler)
    ai_runner = web.AppRunner(ai_app)
    await ai_runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    ai_site = web.SockSite(ai_runner, sock)
    await ai_site.start()

    try:
        agent = SessionAgent(ai_socket=f"http://127.0.0.1:{port}/v1", tools={}, model="test")
        lock = anyio.Lock()

        app = web.Application()
        app["agent"] = agent
        app["lock"] = lock
        app.router.add_post("/v1/chat/completions", handle_chat_completions)

        runner = web.AppRunner(app)
        await runner.setup()
        socket_path = str(tmp_path / "s.sock")
        site = web.UnixSite(runner, socket_path)
        await site.start()

        try:
            await anyio.sleep(0.1)
            connector = UnixConnector(path=socket_path)
            timeout = ClientTimeout(total=5)
            async with (
                ClientSession(connector=connector, timeout=timeout) as s,
                s.post(
                    "http://localhost/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                ) as resp,
            ):
                assert resp.status == 200
        finally:
            await runner.cleanup()
    finally:
        await ai_runner.cleanup()


@pytest.mark.anyio
async def test_agent_run_raises_produces_error_chunk(tmp_path: Path) -> None:
    """When agent.run() raises mid-stream, the server catches it and sends error chunk."""
    agent = _FailingSessionAgent(ai_socket="http://nonexistent/v1", tools={}, model="test")
    lock = anyio.Lock()

    app = web.Application()
    app["agent"] = agent
    app["lock"] = lock
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    socket_path = str(tmp_path / "s.sock")
    site = web.UnixSite(runner, socket_path)
    await site.start()

    try:
        await anyio.sleep(0.1)
        connector = UnixConnector(path=socket_path)
        timeout = ClientTimeout(total=5)
        all_chunks: list[str] = []
        async with (
            ClientSession(connector=connector, timeout=timeout) as s,
            s.post(
                "http://localhost/v1/chat/completions",
                json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            assert resp.status == 200
            async for raw in resp.content:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    all_chunks.append(line[6:])
        all_text = "".join(all_chunks)
        assert "Session Error" in all_text, f"Expected error chunk, got: {all_text[:300]}"
        assert "boom" in all_text.lower(), f"Expected 'boom' in error, got: {all_text[:300]}"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_handle_chat_completions_schedules_after_turn(tmp_path: Path) -> None:
    async def ai_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = json.dumps({"id": "t", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    after_turn_called = anyio.Event()
    after_turn_messages: list[list[dict]] = []

    async def after_turn(messages: list[dict], tool_call_count: int, called_tools: list[str]) -> None:
        _ = tool_call_count
        _ = called_tools
        after_turn_messages.append(messages)
        after_turn_called.set()

    ai_app = web.Application()
    ai_app.router.add_post("/v1/chat/completions", ai_handler)
    ai_runner = web.AppRunner(ai_app)
    await ai_runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    ai_site = web.SockSite(ai_runner, sock)
    await ai_site.start()

    try:
        agent = SessionAgent(
            ai_socket=f"http://127.0.0.1:{port}/v1",
            tools={},
            model="test",
            after_turn_fn=after_turn,
        )
        lock = anyio.Lock()

        app = web.Application()
        app["agent"] = agent
        app["lock"] = lock
        app.router.add_post("/v1/chat/completions", handle_chat_completions)

        try:
            async with anyio.create_task_group() as tg:
                app["after_turn_task_group"] = tg
                runner = web.AppRunner(app)
                await runner.setup()
                socket_path = str(tmp_path / "s.sock")
                site = web.UnixSite(runner, socket_path)
                await site.start()
                await anyio.sleep(0.1)
                connector = UnixConnector(path=socket_path)
                timeout = ClientTimeout(total=5)
                all_chunks: list[str] = []
                async with (
                    ClientSession(connector=connector, timeout=timeout) as s,
                    s.post(
                        "http://localhost/v1/chat/completions",
                        json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                    ) as resp,
                ):
                    assert resp.status == 200
                    async for raw in resp.content:
                        line = raw.decode().strip()
                        if line.startswith("data: "):
                            all_chunks.append(line[6:])
                assert "[DONE]" in all_chunks
                with anyio.fail_after(5):
                    await after_turn_called.wait()
                tg.cancel_scope.cancel()

            assert after_turn_messages[0][-1] == {"role": "assistant", "content": "ok"}
        finally:
            if "runner" in locals():
                await runner.cleanup()
    finally:
        await ai_runner.cleanup()
