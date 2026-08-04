"""End-to-end broadcast aggregation through a real Session agent loop."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web

from psi_agent.router.aggregation import AggregationConfig, AggregationStrategy
from psi_agent.router.client import RouterHttpClient
from psi_agent.router.models import RouterTarget
from psi_agent.router.server import create_router_app
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry


def _chunk(
    *,
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str,
) -> bytes:
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    payload = {
        "id": "mock",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _start_app(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    await web.SockSite(runner, sock).start()
    return runner, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def _start_handler(handler: Any) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    return await _start_app(app)


async def _sse(request: web.Request, *chunks: bytes) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for chunk in chunks:
        await response.write(chunk)
    await response.write(b"data: [DONE]\n\n")
    return response


async def _start_aggregation_router(
    *,
    aggregator_url: str,
    targets: list[RouterTarget],
) -> tuple[web.AppRunner, str]:
    config = AggregationConfig(
        session_socket="router-listener",
        aggregator_socket=aggregator_url,
        targets=targets,
        aggregator_timeout=10,
        target_timeout=10,
    )
    strategy = AggregationStrategy(config=config, client=RouterHttpClient())
    return await _start_app(create_router_app(strategy=strategy))


async def _run_session(
    *,
    router_url: str,
    history_path: Path,
    tools: dict[str, Any] | None = None,
) -> list[Any]:
    tool_functions = {name: ToolFunction.from_callable(function) for name, function in (tools or {}).items()}
    registry = ToolRegistry(files={"test": FileEntry("", tool_functions, tools or {})} if tools else {})
    await anyio.Path(history_path.parent).mkdir(parents=True, exist_ok=True)
    agent = SessionAgent(
        ai_client=AiClient(router_url),
        conversation=Conversation(path=history_path),
        tool_registry=registry,
    )
    return [chunk async for chunk in agent.run({"role": "user", "content": "solve it"})]


@pytest.mark.anyio
async def test_session_aggregation_broadcasts_all_targets_and_tolerates_partial_failure(
    tmp_path: Path,
) -> None:
    requests: dict[str, list[dict[str, Any]]] = {name: [] for name in ("one", "two", "three", "aggregator")}

    async def branch_one(request: web.Request) -> web.StreamResponse:
        requests["one"].append(await request.json())
        return await _sse(request, _chunk(content="answer one", finish="stop"))

    async def branch_two(request: web.Request) -> web.StreamResponse:
        requests["two"].append(await request.json())
        return web.Response(status=503, text="branch unavailable")

    async def branch_three(request: web.Request) -> web.StreamResponse:
        requests["three"].append(await request.json())
        return await _sse(request, _chunk(content="answer three", finish="stop"))

    async def aggregator(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["aggregator"].append(body)
        evidence = json.loads(body["messages"][-1]["content"].split("\n\n", 1)[1])
        feedback = evidence["aggregation_feedback"]
        assert [item["candidate_id"] for item in feedback] == [
            "candidate-1",
            "candidate-2",
            "candidate-3",
        ]
        assert [item["status"] for item in feedback] == ["success", "error", "success"]
        return await _sse(request, _chunk(content="combined partial answer", finish="stop"))

    runners: list[web.AppRunner] = []
    try:
        one_runner, one_url = await _start_handler(branch_one)
        runners.append(one_runner)
        two_runner, two_url = await _start_handler(branch_two)
        runners.append(two_runner)
        three_runner, three_url = await _start_handler(branch_three)
        runners.append(three_runner)
        aggregator_runner, aggregator_url = await _start_handler(aggregator)
        runners.append(aggregator_runner)
        router_runner, router_url = await _start_aggregation_router(
            aggregator_url=aggregator_url,
            targets=[
                RouterTarget("candidate-1", one_url, "first"),
                RouterTarget("candidate-2", two_url, "second"),
                RouterTarget("candidate-3", three_url, "third"),
            ],
        )
        runners.append(router_runner)

        chunks = await _run_session(
            router_url=router_url,
            history_path=tmp_path / "histories" / "partial.jsonl",
        )
    finally:
        for runner in reversed(runners):
            await runner.cleanup()

    assert "".join(chunk.content or "" for chunk in chunks).endswith("combined partial answer")
    assert [len(requests[name]) for name in ("one", "two", "three", "aggregator")] == [
        1,
        1,
        1,
        1,
    ]


@pytest.mark.anyio
async def test_aggregator_tool_round_rebroadcasts_updated_session_history(tmp_path: Path) -> None:
    requests: dict[str, list[dict[str, Any]]] = {name: [] for name in ("one", "two", "aggregator")}
    tool_runs = 0

    async def branch(name: str, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests[name].append(body)
        if len(requests[name]) == 2:
            assert body["messages"][-1]["role"] == "tool"
            assert "lookup-result" in body["messages"][-1]["content"]
        return await _sse(
            request,
            _chunk(content=f"{name} answer round {len(requests[name])}", finish="stop"),
        )

    async def branch_one(request: web.Request) -> web.StreamResponse:
        return await branch("one", request)

    async def branch_two(request: web.Request) -> web.StreamResponse:
        return await branch("two", request)

    async def aggregator(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests["aggregator"].append(body)
        if len(requests["aggregator"]) == 1:
            return await _sse(
                request,
                _chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "aggregate-tool",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                    finish="tool_calls",
                ),
            )
        assert body["messages"][-2]["role"] == "tool"
        return await _sse(request, _chunk(content="combined after tool", finish="stop"))

    async def lookup() -> str:
        nonlocal tool_runs
        tool_runs += 1
        return "lookup-result"

    runners: list[web.AppRunner] = []
    try:
        one_runner, one_url = await _start_handler(branch_one)
        runners.append(one_runner)
        two_runner, two_url = await _start_handler(branch_two)
        runners.append(two_runner)
        aggregator_runner, aggregator_url = await _start_handler(aggregator)
        runners.append(aggregator_runner)
        router_runner, router_url = await _start_aggregation_router(
            aggregator_url=aggregator_url,
            targets=[
                RouterTarget("candidate-1", one_url, "first"),
                RouterTarget("candidate-2", two_url, "second"),
            ],
        )
        runners.append(router_runner)

        chunks = await _run_session(
            router_url=router_url,
            history_path=tmp_path / "histories" / "tool.jsonl",
            tools={"lookup": lookup},
        )
    finally:
        for runner in reversed(runners):
            await runner.cleanup()

    assert tool_runs == 1
    assert "".join(chunk.content or "" for chunk in chunks).endswith("combined after tool")
    assert [len(requests[name]) for name in ("one", "two", "aggregator")] == [2, 2, 2]
