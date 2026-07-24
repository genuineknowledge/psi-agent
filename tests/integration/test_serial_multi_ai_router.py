"""End-to-end serial Router flow through a real Session tool loop."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.router.client import RouterClient
from psi_agent.router.orchestrator import Orchestrator
from psi_agent.router.protocol import RouterConfig
from psi_agent.router.server import (
    _ROUTER_CLIENT_KEY,
    _ROUTER_CONFIG_KEY,
    _ROUTER_ORCHESTRATOR_KEY,
    handle_chat_completions,
)
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry


def _chunk(*, content: str = "", tool_calls: list[dict[str, Any]] | None = None, finish: str) -> bytes:
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    payload = {"id": "mock", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _start(handler: Any) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    await web.SockSite(runner, sock).start()
    return runner, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def _sse(request: web.Request, chunk: bytes) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    await response.write(chunk)
    await response.write(b"data: [DONE]\n\n")
    return response


@pytest.mark.anyio
async def test_session_router_executes_three_planned_subtasks_serially_with_exact_socket_mapping(
    tmp_path: Path,
) -> None:
    """Tools round-trip through Session while Router keeps every branch serial."""

    requests: dict[str, list[dict[str, Any]]] = {name: [] for name in ("router", "one", "two", "three")}

    async def router_ai(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["router"].append(body)
        if len(requests["router"]) == 1:
            plan = json.dumps(
                {
                    "tasks": [
                        {"subtask": "first", "socket": branch_one_url},
                        {"subtask": "second", "socket": branch_two_url},
                        {"subtask": "third", "socket": branch_three_url},
                    ]
                }
            )
            return await _sse(request, _chunk(content=plan, finish="stop"))
        return await _sse(request, _chunk(content="combined", finish="stop"))

    async def branch_one(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["one"].append(body)
        if len(requests["one"]) == 1:
            return await _sse(
                request,
                _chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "one-local",
                            "type": "function",
                            "function": {"name": "first_tool", "arguments": "{}"},
                        }
                    ],
                    finish="tool_calls",
                ),
            )
        assert body["messages"][-1]["content"] == "one-result"
        return await _sse(request, _chunk(content="answer one", finish="stop"))

    async def branch_two(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["two"].append(body)
        if len(requests["two"]) == 1:
            return await _sse(
                request,
                _chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "two-local",
                            "type": "function",
                            "function": {"name": "second_tool", "arguments": "{}"},
                        }
                    ],
                    finish="tool_calls",
                ),
            )
        assert body["messages"][-1]["content"] == "two-result"
        return await _sse(request, _chunk(content="answer two", finish="stop"))

    async def branch_three(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["three"].append(body)
        return await _sse(request, _chunk(content="answer three", finish="stop"))

    one_runner, branch_one_url = await _start(branch_one)
    two_runner, branch_two_url = await _start(branch_two)
    three_runner, branch_three_url = await _start(branch_three)
    router_ai_runner, router_ai_url = await _start(router_ai)
    router_config = RouterConfig(
        session_socket="router-listener",
        router_socket=router_ai_url,
        default_socket=router_ai_url,
        upstream=[(branch_one_url, "first"), (branch_two_url, "second"), (branch_three_url, "third")],
    )
    client = RouterClient()
    router_app = web.Application()
    router_app[_ROUTER_CONFIG_KEY] = router_config
    router_app[_ROUTER_CLIENT_KEY] = client
    router_app[_ROUTER_ORCHESTRATOR_KEY] = Orchestrator(config=router_config, client=client)
    router_app.router.add_post("/chat/completions", handle_chat_completions)
    router_runner = web.AppRunner(router_app)
    await router_runner.setup()
    router_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    router_sock.bind(("127.0.0.1", 0))
    await web.SockSite(router_runner, router_sock).start()

    async def first_tool() -> str:
        return "one-result"

    async def second_tool() -> str:
        return "two-result"

    history_path = tmp_path / "histories" / "serial-session.jsonl"
    await anyio.Path(history_path.parent).mkdir()
    tools = {
        "first_tool": ToolFunction.from_callable(first_tool),
        "second_tool": ToolFunction.from_callable(second_tool),
    }
    agent = SessionAgent(
        ai_client=AiClient(f"http://127.0.0.1:{router_sock.getsockname()[1]}"),
        conversation=Conversation(path=history_path),
        tool_registry=ToolRegistry(
            files={"test": FileEntry("", tools, {"first_tool": first_tool, "second_tool": second_tool})}
        ),
    )
    try:
        chunks = [chunk async for chunk in agent.run({"role": "user", "content": "solve it"})]
    finally:
        await router_runner.cleanup()
        await router_ai_runner.cleanup()
        await three_runner.cleanup()
        await two_runner.cleanup()
        await one_runner.cleanup()

    assert "".join(chunk.content or "" for chunk in chunks).endswith("combined")
    assert [len(requests[name]) for name in ("router", "one", "two", "three")] == [2, 1, 1, 1]
