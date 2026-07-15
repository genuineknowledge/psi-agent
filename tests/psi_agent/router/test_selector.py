from __future__ import annotations

import json

import anyio
import pytest
from aiohttp import web
from aiohttp.typedefs import Handler

from psi_agent.router.models import RouteDecision, Upstream
from psi_agent.router.selector import (
    RouterSelectionError,
    build_routing_messages,
    parse_decision,
    select_upstream,
    serialize_context,
)

TARGETS = (
    Upstream("secret-model-a", "http://secret-a:1", "simple Chinese tasks"),
    Upstream("secret-model-b", "http://secret-b:2", "code and mathematics"),
)


def test_prompt_exposes_only_candidate_numbers_and_descriptions() -> None:
    messages = build_routing_messages("[USER]\nprove this", TARGETS)
    rendered = "\n".join(item["content"] for item in messages)
    assert "Candidate 0: simple Chinese tasks" in rendered
    assert "Candidate 1: code and mathematics" in rendered
    assert "secret-model" not in rendered
    assert "http://secret" not in rendered


@pytest.mark.parametrize(
    "text",
    [
        '{"candidate":1,"reason":"math"}',
        '```json\n{"candidate":1,"reason":"math"}\n```',
        'selection follows: {"candidate":1,"reason":"math"}',
    ],
)
def test_parse_decision_extracts_first_valid_object(text: str) -> None:
    assert parse_decision(text, candidate_count=2) == RouteDecision(1, "math")


@pytest.mark.parametrize("value", [True, -1, 2, "1"])
def test_parse_decision_rejects_invalid_candidate(value: object) -> None:
    text = json.dumps({"candidate": value, "reason": "x"})
    with pytest.raises(ValueError, match="valid candidate"):
        parse_decision(text, candidate_count=2)


def test_parse_decision_skips_invalid_object_before_valid_object() -> None:
    text = '{"candidate":9} then {"candidate":0,"reason":"valid"}'
    assert parse_decision(text, candidate_count=2) == RouteDecision(0, "valid")


def test_serialize_context_keeps_system_and_latest_user_within_limit() -> None:
    messages = [
        {"role": "system", "content": "system rule"},
        {"role": "user", "content": "old " * 50},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest question"},
    ]
    context = serialize_context(messages, max_chars=80)
    assert len(context) <= 80
    assert "system rule" in context
    assert "latest question" in context


def test_serialize_context_marks_tools_and_multimodal_content() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "inspect"}, {"type": "image_url"}]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "search", "arguments": "secret arguments"}}],
        },
        {"role": "tool", "content": "large secret result"},
    ]
    context = serialize_context(messages, max_chars=500)
    assert "[IMAGE]" in context
    assert "search" in context
    assert "secret" not in context
    assert "Tool results exist" in context


def test_serialize_context_requires_user_content() -> None:
    messages = [{"role": "system", "content": "rules"}, {"role": "assistant", "content": "hello"}]
    assert serialize_context(messages, max_chars=100) == ""


def test_serialize_context_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        serialize_context([], max_chars=0)


async def _serve_router_model(handler: Handler, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


@pytest.mark.anyio
async def test_select_upstream_calls_openai_compatible_router(unused_tcp_port: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        assert request.headers["Authorization"] == "Bearer router-key"
        assert body["model"] == "router-model"
        assert body["stream"] is False
        rendered = "\n".join(item["content"] for item in body["messages"])
        assert "simple Chinese tasks" in rendered
        assert "secret-model" not in rendered
        return web.json_response({"choices": [{"message": {"content": '{"candidate":0,"reason":"simple"}'}}]})

    runner = await _serve_router_model(handler, unused_tcp_port)
    try:
        decision = await select_upstream(
            context="[USER]\nsummarize this",
            targets=TARGETS,
            router_model="router-model",
            router_base_url=f"http://127.0.0.1:{unused_tcp_port}/v1",
            router_api_key="router-key",
            router_timeout=None,
        )
        assert decision == RouteDecision(0, "simple")
    finally:
        await runner.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"content": 7}}]},
        {"choices": [{"message": {"content": "not-json"}}]},
    ],
)
async def test_select_upstream_rejects_incompatible_responses(payload: object, unused_tcp_port: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(payload)

    runner = await _serve_router_model(handler, unused_tcp_port)
    try:
        with pytest.raises(RouterSelectionError):
            await select_upstream(
                context="[USER]\ntask",
                targets=TARGETS,
                router_model="router-model",
                router_base_url=f"http://127.0.0.1:{unused_tcp_port}/v1",
                router_api_key="",
                router_timeout=None,
            )
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_select_upstream_rejects_http_error(unused_tcp_port: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=503, text="unavailable")

    runner = await _serve_router_model(handler, unused_tcp_port)
    try:
        with pytest.raises(RouterSelectionError, match="HTTP 503"):
            await select_upstream(
                context="[USER]\ntask",
                targets=TARGETS,
                router_model="router-model",
                router_base_url=f"http://127.0.0.1:{unused_tcp_port}/v1",
                router_api_key="",
                router_timeout=None,
            )
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_select_upstream_enforces_timeout(unused_tcp_port: int) -> None:
    async def handler(request: web.Request) -> web.Response:
        await anyio.sleep(1)
        return web.json_response({})

    runner = await _serve_router_model(handler, unused_tcp_port)
    try:
        with pytest.raises(RouterSelectionError, match="timed out"):
            await select_upstream(
                context="[USER]\ntask",
                targets=TARGETS,
                router_model="router-model",
                router_base_url=f"http://127.0.0.1:{unused_tcp_port}/v1",
                router_api_key="",
                router_timeout=0.01,
            )
    finally:
        await runner.cleanup()
