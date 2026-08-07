"""Real HTTP/Session coverage for freely composable Router modes."""

from __future__ import annotations

import itertools
import json
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import aclosing
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from psi_agent.router.aggregation import AggregationConfig, AggregationStrategy
from psi_agent.router.client import RouterHttpClient
from psi_agent.router.fallback import FallbackConfig, FallbackStrategy
from psi_agent.router.models import RouterMode, RouterTarget
from psi_agent.router.request import routing_scope_from_body
from psi_agent.router.routing import RouteSelector, RoutingConfig, RoutingStrategy
from psi_agent.router.server import RouterStrategy, create_router_app
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.tool_registry import ToolRegistry


def _aggregation_feedback(content: str) -> list[dict[str, Any]]:
    serialized = content.split("<aggregation_feedback_json>\n", maxsplit=1)[1].split(
        "\n</aggregation_feedback_json>", maxsplit=1
    )[0]
    value = json.loads(serialized)["aggregation_feedback"]
    assert isinstance(value, list)
    return value


class RecordingStrategy:
    """Record public Router requests without changing strategy behavior."""

    def __init__(self, inner: RouterStrategy) -> None:
        self.inner = inner
        self.bodies: list[dict[str, Any]] = []

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        self.bodies.append(deepcopy(body))
        stream = self.inner.stream(body=body)
        async with aclosing(stream) as events:
            async for event in events:
                yield event

    def discard(self, session_id: str) -> None:
        self.inner.discard(session_id)

    def clear(self) -> None:
        self.inner.clear()


def _chunk(*, content: str, finish_reason: str = "stop") -> bytes:
    payload = {
        "id": "composition",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _sse(request: web.Request, *chunks: bytes) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for chunk in chunks:
        await response.write(chunk)
    await response.write(b"data: [DONE]\n\n")
    return response


async def _start_app(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    await web.SockSite(runner, sock).start()
    return runner, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def _start_handler(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    return await _start_app(app)


async def _run_session(*, router_url: str, history_path: Path) -> list[Any]:
    agent = SessionAgent(
        ai_client=AiClient(router_url),
        conversation=Conversation(path=history_path),
        tool_registry=ToolRegistry(files={}),
    )
    return [chunk async for chunk in agent.run({"role": "user", "content": "solve"})]


async def _start_linear_chain(
    modes: Sequence[RouterMode],
) -> tuple[
    list[web.AppRunner],
    str,
    list[RecordingStrategy],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    runners: list[web.AppRunner] = []
    leaf_requests: list[dict[str, Any]] = []
    selector_requests: list[dict[str, Any]] = []
    aggregator_requests: list[dict[str, Any]] = []

    async def leaf(request: web.Request) -> web.StreamResponse:
        leaf_requests.append(await request.json())
        return await _sse(request, _chunk(content="leaf answer"))

    async def selector(request: web.Request) -> web.StreamResponse:
        selector_requests.append(await request.json())
        return await _sse(request, _chunk(content='{"candidate_id":"edge"}'))

    async def aggregator(request: web.Request) -> web.StreamResponse:
        aggregator_requests.append(await request.json())
        return await _sse(request, _chunk(content="combined answer"))

    try:
        runner, current_url = await _start_handler(leaf)
        runners.append(runner)
        runner, selector_url = await _start_handler(selector)
        runners.append(runner)
        runner, aggregator_url = await _start_handler(aggregator)
        runners.append(runner)

        recorded_by_position: list[RecordingStrategy | None] = [None] * len(modes)
        client = RouterHttpClient()
        for position in reversed(range(len(modes))):
            backend_type = "ai" if position == len(modes) - 1 else "router"
            target = RouterTarget(
                "edge",
                current_url,
                "next layer",
                backend_type=backend_type,
            )
            mode = modes[position]
            if mode is RouterMode.ROUTING:
                config = RoutingConfig(
                    session_socket=f"routing-{position}.sock",
                    selector_socket=selector_url,
                    targets=[target],
                )
                inner: RouterStrategy = RoutingStrategy(
                    config=config,
                    selector=RouteSelector(config=config, client=client),
                    client=client,
                )
            elif mode is RouterMode.AGGREGATION:
                inner = AggregationStrategy(
                    config=AggregationConfig(
                        session_socket=f"aggregation-{position}.sock",
                        aggregator_socket=aggregator_url,
                        targets=[target],
                    ),
                    client=client,
                )
            else:
                inner = FallbackStrategy(
                    config=FallbackConfig(
                        session_socket=f"fallback-{position}.sock",
                        targets=[target],
                    ),
                    client=client,
                )
            recorder = RecordingStrategy(inner)
            recorded_by_position[position] = recorder
            runner, current_url = await _start_app(create_router_app(strategy=recorder))
            runners.append(runner)
    except BaseException:
        for runner in reversed(runners):
            await runner.cleanup()
        raise

    assert all(recorder is not None for recorder in recorded_by_position)
    return (
        runners,
        current_url,
        [recorder for recorder in recorded_by_position if recorder is not None],
        leaf_requests,
        selector_requests,
        aggregator_requests,
    )


_PAIR_MATRIX = list(itertools.product(RouterMode, repeat=2))
_THREE_MODE_PERMUTATIONS = list(itertools.permutations(RouterMode))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "modes",
    [*_PAIR_MATRIX, *_THREE_MODE_PERMUTATIONS],
    ids=lambda modes: "-".join(mode.value for mode in modes),
)
async def test_real_session_supports_pair_matrix_and_three_mode_permutations(
    modes: tuple[RouterMode, ...],
    tmp_path: Path,
) -> None:
    runners, router_url, recorders, leaf_requests, selector_requests, aggregator_requests = await _start_linear_chain(
        modes
    )
    try:
        chunks = await _run_session(
            router_url=router_url,
            history_path=tmp_path / "composition.jsonl",
        )
    finally:
        for runner in reversed(runners):
            await runner.cleanup()

    expected = "combined answer" if RouterMode.AGGREGATION in modes else "leaf answer"
    assert "".join(chunk.content or "" for chunk in chunks).endswith(expected)
    assert len(leaf_requests) == 1
    assert "routing" not in leaf_requests[0]
    assert all("routing" not in body for body in selector_requests)
    assert all("routing" not in body for body in aggregator_requests)
    for position, recorder in enumerate(recorders):
        assert len(recorder.bodies) == 1
        assert routing_scope_from_body(body=recorder.bodies[0]) == (
            "composition",
            ("edge",) * position,
        )


@pytest.mark.anyio
async def test_aggregation_branch_graph_treats_child_router_error_as_one_failed_branch(
    tmp_path: Path,
) -> None:
    runners: list[web.AppRunner] = []
    failed_leaf_requests: list[dict[str, Any]] = []
    successful_leaf_requests: list[dict[str, Any]] = []
    aggregation_feedback: list[dict[str, Any]] = []

    async def failed_leaf(request: web.Request) -> web.StreamResponse:
        failed_leaf_requests.append(await request.json())
        return web.Response(status=503, text="unavailable")

    async def successful_leaf(request: web.Request) -> web.StreamResponse:
        successful_leaf_requests.append(await request.json())
        return await _sse(request, _chunk(content="branch answer"))

    async def selector(request: web.Request) -> web.StreamResponse:
        await request.json()
        return await _sse(request, _chunk(content='{"candidate_id":"leaf"}'))

    async def aggregator(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        aggregation_feedback.extend(_aggregation_feedback(body["messages"][-1]["content"]))
        return await _sse(request, _chunk(content="branch graph combined"))

    try:
        runner, failed_url = await _start_handler(failed_leaf)
        runners.append(runner)
        runner, successful_url = await _start_handler(successful_leaf)
        runners.append(runner)
        runner, selector_url = await _start_handler(selector)
        runners.append(runner)
        runner, aggregator_url = await _start_handler(aggregator)
        runners.append(runner)

        client = RouterHttpClient()
        failed_child = RecordingStrategy(
            FallbackStrategy(
                config=FallbackConfig(
                    session_socket="failed-child.sock",
                    targets=[RouterTarget("failed-leaf", failed_url, "fails")],
                ),
                client=client,
            )
        )
        runner, failed_child_url = await _start_app(create_router_app(strategy=failed_child))
        runners.append(runner)

        routing_config = RoutingConfig(
            session_socket="successful-child.sock",
            selector_socket=selector_url,
            targets=[RouterTarget("leaf", successful_url, "works")],
        )
        successful_child = RecordingStrategy(
            RoutingStrategy(
                config=routing_config,
                selector=RouteSelector(config=routing_config, client=client),
                client=client,
            )
        )
        runner, successful_child_url = await _start_app(create_router_app(strategy=successful_child))
        runners.append(runner)

        outer = RecordingStrategy(
            AggregationStrategy(
                config=AggregationConfig(
                    session_socket="outer.sock",
                    aggregator_socket=aggregator_url,
                    targets=[
                        RouterTarget(
                            "failed-child",
                            failed_child_url,
                            "failure branch",
                            backend_type="router",
                        ),
                        RouterTarget(
                            "successful-child",
                            successful_child_url,
                            "success branch",
                            backend_type="router",
                        ),
                    ],
                ),
                client=client,
            )
        )
        runner, outer_url = await _start_app(create_router_app(strategy=outer))
        runners.append(runner)

        chunks = await _run_session(
            router_url=outer_url,
            history_path=tmp_path / "branch-graph.jsonl",
        )
    finally:
        for runner in reversed(runners):
            await runner.cleanup()

    assert "".join(chunk.content or "" for chunk in chunks).endswith("branch graph combined")
    assert [item["status"] for item in aggregation_feedback] == ["error", "success"]
    assert failed_child.bodies[0]["routing"]["path"] == ["failed-child"]
    assert successful_child.bodies[0]["routing"]["path"] == ["successful-child"]
    assert "routing" not in failed_leaf_requests[0]
    assert "routing" not in successful_leaf_requests[0]
