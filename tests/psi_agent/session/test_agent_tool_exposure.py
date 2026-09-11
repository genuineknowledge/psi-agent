"""Exposure at the agent layer: what the ``tools`` array in a real request holds.

The unit and registry criteria stop at ``select_exposed``. Nothing there shows
that a ``SessionAgent`` actually *applies* it, and past cards in this repo have
shipped criteria that claimed one layer while calling another — so these run a
turn against a mock AI server and assert on the request body it received. If the
wiring in ``agent.py`` were removed, the functions those other files exercise
would still be correct and still be green; only this file goes red.

The gear is read from the environment at ``SessionAgent`` construction, so each
criterion sets ``PSI_TOOL_EXPOSURE`` with ``monkeypatch`` *before* building the
agent. A criterion that set it afterwards would pass for the wrong reason (the
default gear is the narrowing one), which is why the un-narrowed gear is the one
carrying the "the switch works" claim.
"""

from __future__ import annotations

import json
import socket as _s
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.tool_exposure import EXPOSURE_TIER_ENV, MANIFEST_NAME
from psi_agent.session.tool_registry import ToolRegistry


def _sse(content: str) -> bytes:
    chunk = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


class _MockAI:
    """Records the request bodies a turn sends, over a real HTTP socket."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._runner: web.AppRunner | None = None

    async def start(self) -> str:
        async def handler(request: web.Request) -> web.StreamResponse:
            self.requests.append(await request.json())
            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(_sse("ok"))
            await response.write(b"data: [DONE]\n\n")
            return response

        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        return f"http://127.0.0.1:{port}"

    async def cleanup(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


def _write_tools(tools_dir: Path, *names: str) -> None:
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (tools_dir / f"{name}.py").write_text(
            f'''
async def {name}() -> str:
    """Tool {name}."""
    return "{name}"
''',
            encoding="utf-8",
        )


async def _exposed_names_in_request(tmp_path: Path, tools_dir: Path) -> set[str]:
    """Run one turn and return the tool names the request's ``tools`` array held."""
    registry = await ToolRegistry.load(tools_dir)
    server = _MockAI()
    socket = await server.start()
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket),
            conversation=Conversation(path=tmp_path / "session.jsonl"),
            tool_registry=registry,
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "hi"})]
    finally:
        await server.cleanup()

    assert server.requests, "mock AI received no request"
    return {entry["function"]["name"] for entry in server.requests[0].get("tools", [])}


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    """A tools dir with a manifest naming one of its three tools."""
    tools_dir = tmp_path / "tools"
    _write_tools(tools_dir, "declared_tool", "hidden_tool", "tool_search")
    (tools_dir / MANIFEST_NAME).write_text("declared_tool\n", encoding="utf-8")
    return tools_dir


async def test_default_gear_sends_only_the_declared_tools_plus_discovery(
    tmp_path: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped behaviour, measured where it is billed: in the request body."""
    monkeypatch.delenv(EXPOSURE_TIER_ENV, raising=False)

    names = await _exposed_names_in_request(tmp_path, pack)

    assert names == {"declared_tool", "tool_search"}


async def test_off_gear_sends_the_full_surface(tmp_path: Path, pack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The switch, proven at the layer that has to honour it: flipping the env var
    changes the array a request carries, with no code change and no redeploy."""
    monkeypatch.setenv(EXPOSURE_TIER_ENV, "off")

    names = await _exposed_names_in_request(tmp_path, pack)

    assert names == {"declared_tool", "hidden_tool", "tool_search"}


async def test_declared_gear_sends_the_floor(tmp_path: Path, pack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPOSURE_TIER_ENV, "declared")

    names = await _exposed_names_in_request(tmp_path, pack)

    assert names == {"declared_tool", "tool_search"}


async def test_a_tool_absent_from_the_request_is_still_callable_in_that_session(
    tmp_path: Path, pack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery, not capability — asserted against the same Session that sent the
    narrowed array, rather than against a registry built on the side.

    This is the one that would catch a future "optimisation" that narrowed the
    registry instead of the array: the request shows ``hidden_tool`` was not
    offered, and the Session's own registry still runs it.
    """
    monkeypatch.delenv(EXPOSURE_TIER_ENV, raising=False)
    registry = await ToolRegistry.load(pack)
    server = _MockAI()
    socket = await server.start()
    try:
        agent = SessionAgent(
            ai_client=AiClient(socket),
            conversation=Conversation(path=tmp_path / "session.jsonl"),
            tool_registry=registry,
        )
        _ = [chunk async for chunk in agent.run({"role": "user", "content": "hi"})]
    finally:
        await server.cleanup()

    sent = {entry["function"]["name"] for entry in server.requests[0]["tools"]}
    assert "hidden_tool" not in sent

    hidden = registry.get("hidden_tool")
    assert hidden is not None, "narrowing the array also narrowed dispatch"
    assert await hidden() == "hidden_tool"


async def test_an_unset_manifest_exposes_the_whole_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary workspace declares nothing and is not narrowed — the case that
    makes this safe to turn on by default."""
    monkeypatch.delenv(EXPOSURE_TIER_ENV, raising=False)
    tools_dir = tmp_path / "tools"
    _write_tools(tools_dir, "alpha", "beta", "gamma")

    names = await _exposed_names_in_request(tmp_path, tools_dir)

    assert names == {"alpha", "beta", "gamma"}
