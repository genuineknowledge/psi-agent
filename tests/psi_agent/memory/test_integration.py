"""Tests for Fusion Memory integration helpers."""

from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.memory.client import FusionMemoryClient, FusionMemoryError
from psi_agent.memory.config import (
    MAX_MEMORY_INJECT_MAX_CHARS,
    MAX_MEMORY_TIMEOUT_SECONDS,
    MemoryConfig,
)
from psi_agent.memory.formatting import format_memory_context
from psi_agent.memory.scope import build_memory_scope
from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice
from psi_agent.session.tools import load_tool_callables_from_workspace, load_tools_from_workspace


def test_memory_client_default_timeout_stays_below_first_token_budget() -> None:
    client = FusionMemoryClient("http://127.0.0.1:8765")

    assert client.timeout.total == pytest.approx(1.0)
    assert client.timeout.total < 2.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 1.0),
        ("999", MAX_MEMORY_TIMEOUT_SECONDS),
        ("0", 1.0),
        ("-1", 1.0),
        ("not-a-number", 1.0),
        ("inf", 1.0),
        ("nan", 1.0),
    ],
)
def test_tool_api_timeout_env_is_clamped_to_fail_open_budget(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: float,
) -> None:
    from psi_agent.memory import tool_api

    if value is None:
        monkeypatch.delenv("PSI_MEMORY_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("PSI_MEMORY_TIMEOUT_SECONDS", value)

    assert tool_api._timeout_seconds() == pytest.approx(expected)
    assert tool_api._timeout_seconds() <= MAX_MEMORY_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 12000),
        ("1000000", MAX_MEMORY_INJECT_MAX_CHARS),
        ("0", 12000),
        ("-1", 12000),
        ("not-a-number", 12000),
    ],
)
def test_tool_api_inject_max_chars_env_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: int,
) -> None:
    from psi_agent.memory import tool_api

    if value is None:
        monkeypatch.delenv("PSI_MEMORY_INJECT_MAX_CHARS", raising=False)
    else:
        monkeypatch.setenv("PSI_MEMORY_INJECT_MAX_CHARS", value)

    assert tool_api._inject_max_chars() == expected
    assert 0 < tool_api._inject_max_chars() <= MAX_MEMORY_INJECT_MAX_CHARS


def test_memory_config_reads_bounded_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_MEMORY_ENABLED", "true")
    monkeypatch.setenv("PSI_MEMORY_TIMEOUT_SECONDS", "999")
    monkeypatch.setenv("PSI_MEMORY_RETRIEVAL_LIMIT", "999")
    monkeypatch.setenv("PSI_MEMORY_INJECT_MAX_CHARS", "999999")

    config = MemoryConfig.from_env("/tmp/workspace")

    assert config.memory_enabled is True
    assert config.memory_timeout_seconds == pytest.approx(MAX_MEMORY_TIMEOUT_SECONDS)
    assert config.memory_retrieval_limit == 50
    assert config.memory_inject_max_chars == MAX_MEMORY_INJECT_MAX_CHARS


@pytest.mark.anyio
async def test_memory_client_health_add_search_and_answer_context() -> None:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def add(request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["scope"]["workspace_id"] == "w"
        return web.json_response({"span_ids": ["span_1"], "accepted_fact_ids": ["fact_1"]})

    async def search(_request: web.Request) -> web.Response:
        return web.json_response({"candidates": [{"text": "prefers Qdrant"}], "trace_id": "t"})

    async def answer_context(_request: web.Request) -> web.Response:
        return web.json_response({"query": "q", "facts": [{"text": "User prefers Qdrant."}]})

    async def clear(request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["scope"]["workspace_id"] == "w"
        assert payload["allow_cross_session"] is True
        return web.json_response({"ok": True, "deleted": {"evidence_spans": 1}})

    app.router.add_get("/health", health)
    app.router.add_post("/add", add)
    app.router.add_post("/search", search)
    app.router.add_post("/answer-context", answer_context)
    app.router.add_post("/clear", clear)

    async with _running_app(app) as base_url:
        async with FusionMemoryClient(base_url) as client:
            assert (await client.health())["ok"] is True
            added = await client.add({"role": "user", "content": "x"}, {"workspace_id": "w"})
            assert added["accepted_fact_ids"] == ["fact_1"]
            found = await client.search("q", {"workspace_id": "w"})
            assert found["candidates"][0]["text"] == "prefers Qdrant"
            pack = await client.answer_context("q", {"workspace_id": "w"})
            assert pack["facts"][0]["text"] == "User prefers Qdrant."
            cleared = await client.clear({"workspace_id": "w"}, allow_cross_session=True)
            assert cleared["ok"] is True


@pytest.mark.anyio
async def test_memory_client_sanitizes_errors() -> None:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "error": "db failed for postgresql://fusion:secret@127.0.0.1/memory",
                "path": "/internal/memory/store.py",
            },
            status=503,
        )

    app.router.add_get("/health", health)
    async with _running_app(app) as base_url:
        async with FusionMemoryClient(base_url) as client:
            with pytest.raises(FusionMemoryError) as exc_info:
                await client.health()

    message = str(exc_info.value)
    assert message == "Fusion Memory request failed with HTTP 503."
    assert "secret" not in message
    assert "postgresql://" not in message
    assert "/internal" not in message


@pytest.mark.anyio
async def test_memory_client_sanitizes_timeout_errors() -> None:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        await anyio.sleep(0.2)
        return web.json_response({"ok": True})

    app.router.add_get("/health", health)
    async with _running_app(app) as base_url:
        async with FusionMemoryClient(base_url, timeout_seconds=0.01) as client:
            with pytest.raises(FusionMemoryError) as exc_info:
                await client.health()

    assert str(exc_info.value) == "Fusion Memory request timed out."


def test_build_memory_scope_uses_workspace_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PSI_MEMORY_USER_ID", "u")
    monkeypatch.setenv("PSI_MEMORY_AGENT_ID", "a")
    monkeypatch.setenv("PSI_MEMORY_SESSION_ID", "s")

    scope = build_memory_scope(tmp_path)

    assert scope["workspace_id"] == tmp_path.name
    assert scope["user_id"] == "u"
    assert scope["agent_id"] == "a"
    assert scope["session_id"] == "s"


def test_format_memory_context_renders_and_truncates() -> None:
    rendered = format_memory_context(
        {
            "query": "q",
            "facts": [{"text": "User prefers Qdrant."}],
            "events": [{"description": "Deployed memory service", "time_start": "2026-06-21"}],
        },
        max_chars=400,
    )

    assert rendered is not None
    assert "Retrieved Memory Context" in rendered
    assert "User prefers Qdrant" in rendered
    assert len(rendered) <= 400


@pytest.mark.anyio
async def test_memory_tool_wrappers_load_with_filename_or_tool_function(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "memory.py").write_text(
        textwrap.dedent(
            """\
            from psi_agent.memory.tool_api import memory_action

            async def memory(action: str = "read", content: str = "") -> str:
                return await memory_action(action=action, content=content)
            """
        )
    )
    (tools_dir / "memory_read.py").write_text(
        textwrap.dedent(
            """\
            async def tool(query: str = "") -> str:
                return query or "default"
            """
        )
    )

    tools = await load_tools_from_workspace(tools_dir)
    callables = await load_tool_callables_from_workspace(tools_dir)

    assert set(tools) == {"memory", "memory_read"}
    assert set(callables) == {"memory", "memory_read"}
    assert await callables["memory_read"](query="psi") == "psi"


@pytest.mark.anyio
async def test_session_agent_injects_and_records_memory_context() -> None:
    memory = _FakeMemoryAdapter()
    agent = SessionAgent(ai_socket="unused", tools={}, model="test", memory_adapter=memory)

    async def fake_stream(request_body: dict[str, Any]) -> AsyncIterator[ChatCompletionChunk]:
        memory.seen_messages = list(request_body["messages"])
        yield ChatCompletionChunk(
            id="mock",
            model="test",
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaMessage(content="answer"),
                    finish_reason="stop",
                )
            ],
        )

    agent._stream_ai_request = fake_stream  # type: ignore[method-assign]

    chunks = [chunk async for chunk in agent.run({"role": "user", "content": "question"})]

    assert chunks[0].choices[0].delta.content == "answer"
    assert memory.seen_messages[0]["role"] == "system"
    assert "remembered fact" in memory.seen_messages[0]["content"]
    assert memory.recorded == [("question", "answer")]
    assert all("remembered fact" not in str(message.get("content", "")) for message in agent.history)


class _FakeMemoryAdapter:
    def __init__(self) -> None:
        self.seen_messages: list[dict[str, Any]] = []
        self.recorded: list[tuple[str, str]] = []

    async def retrieve_for_turn(self, _user_message: dict[str, Any]) -> str:
        return "remembered fact"

    async def record_turn(self, user_message: dict[str, Any], assistant_content: str) -> None:
        self.recorded.append((str(user_message["content"]), assistant_content))

    async def close(self) -> None:
        pass


@asynccontextmanager
async def _running_app(app: web.Application) -> AsyncIterator[str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    assert sockets is not None
    try:
        yield "http://127.0.0.1:{}".format(sockets[0].getsockname()[1])
    finally:
        await runner.cleanup()
