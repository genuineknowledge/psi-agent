from __future__ import annotations

import json
import socket
from typing import Any

import anyio
import pytest
import tyro
from aiohttp import ClientSession, ClientTimeout, web

import psi_agent.ai.router as router_module
from psi_agent.ai.llmrouter_adapter import RouteDecision, RouteTarget
from psi_agent.ai.router import (
    _CONTEXT_CHARS_KEY,
    _FALLBACK_KEY,
    _LLMROUTER_KEY,
    _LOG_DETAILS_KEY,
    _ROUTE_TARGETS_KEY,
    _ROUTER_TIMEOUT_KEY,
    AiRouter,
    handle_router_chat_completions,
)


def test_ai_router_upstream_defaults_are_not_shared() -> None:
    first = AiRouter(session_socket="http://127.0.0.1:8100")
    second = AiRouter(session_socket="http://127.0.0.1:8101")

    first.upstream.append('{"addr":"a","model_name":"m","description":"d"}')

    assert second.upstream == []


@pytest.mark.anyio
async def test_ai_router_rejects_empty_upstream_list() -> None:
    router = AiRouter(
        session_socket="http://127.0.0.1:8100",
        router_model="router-small",
        router_base_url="https://router.example/v1",
        upstream=[],
    )

    with pytest.raises(ValueError, match="at least one"):
        await router.run()


def test_tyro_accepts_one_upstream_option_with_multiple_json_values() -> None:
    first = '{"addr":"http://127.0.0.1:8101","model_name":"qwen-plus","description":"General"}'
    second = '{"addr":"http://127.0.0.1:8102","model_name":"reasoner","description":"Reasoning"}'

    router = tyro.cli(
        AiRouter,
        args=[
            "--session-socket",
            "http://127.0.0.1:8100",
            "--router-model",
            "router-small",
            "--router-base-url",
            "https://router.example/v1",
            "--upstream",
            first,
            second,
            "--default-addr",
            "127.0.0.1:8101",
        ],
    )

    assert router.upstream == [first, second]
    assert router.default_addr == "127.0.0.1:8101"


class FakeAdapter:
    def __init__(self, decision: RouteDecision | Exception, *, delay: float = 0) -> None:
        self.decision = decision
        self.delay = delay
        self.calls: list[str] = []

    async def route(self, context: str) -> RouteDecision:
        self.calls.append(context)
        if self.delay:
            await anyio.sleep(self.delay)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


async def _listen(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    await web.SockSite(runner, listener).start()
    return runner, f"http://127.0.0.1:{port}"


async def _start_upstream(label: str) -> tuple[web.AppRunner, str, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert isinstance(body, dict)
        requests.append(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        chunks = [
            {"choices": [{"index": 0, "delta": {"reasoning": f"{label}-thinking"}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0}]}}]},
            {"choices": [{"index": 0, "delta": {"content": label}, "finish_reason": "stop"}]},
        ]
        for chunk in chunks:
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner, url = await _listen(app)
    return runner, url, requests


async def _start_router(
    targets: list[RouteTarget],
    adapter: FakeAdapter,
    *,
    fallback: RouteDecision,
    router_timeout: float | None = None,
) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app[_ROUTE_TARGETS_KEY] = targets
    app[_LLMROUTER_KEY] = adapter
    app[_FALLBACK_KEY] = fallback
    app[_ROUTER_TIMEOUT_KEY] = router_timeout
    app[_CONTEXT_CHARS_KEY] = 12_000
    app[_LOG_DETAILS_KEY] = False
    app.router.add_post("/chat/completions", handle_router_chat_completions)
    return await _listen(app)


async def _post(url: str, body: dict[str, Any]) -> tuple[int, str]:
    async with (
        ClientSession(timeout=ClientTimeout(total=5)) as session,
        session.post(f"{url}/chat/completions", json=body) as response,
    ):
        return response.status, await response.text()


@pytest.mark.anyio
async def test_router_proxies_only_to_llmrouter_winner_and_preserves_sse() -> None:
    first_runner, first_url, first_requests = await _start_upstream("first")
    second_runner, second_url, second_requests = await _start_upstream("second")
    targets = [RouteTarget(first_url, "cheap", "cheap"), RouteTarget(second_url, "strong", "strong")]
    decision = RouteDecision(targets[1], (("reason", "strong"),), {"strong": 1})
    adapter = FakeAdapter(decision)
    router_runner, router_url = await _start_router(
        targets, adapter, fallback=RouteDecision(targets[0], (), {}, "fallback_first")
    )
    body = {
        "messages": [{"role": "user", "content": "solve"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "routing": {"ignored": True},
    }
    try:
        status, text = await _post(router_url, body)
        assert status == 200
        assert first_requests == []
        assert len(second_requests) == 1
        sent = second_requests[0]
        assert sent["model"] == "strong"
        assert sent["tools"] == body["tools"]
        assert sent["temperature"] == 0.2
        assert "routing" not in sent
        assert "second-thinking" in text
        assert "tool_calls" in text
        assert "second" in text
        assert "data: [DONE]" in text
    finally:
        await router_runner.cleanup()
        await first_runner.cleanup()
        await second_runner.cleanup()


@pytest.mark.anyio
async def test_matching_request_model_bypasses_llmrouter() -> None:
    runner, upstream_url, requests = await _start_upstream("chosen")
    target = RouteTarget(upstream_url, "chosen", "chosen")
    adapter = FakeAdapter(RuntimeError("must not run"))
    router_runner, router_url = await _start_router(
        [target], adapter, fallback=RouteDecision(target, (), {}, "fallback_first")
    )
    try:
        status, _ = await _post(
            router_url,
            {"model": upstream_url, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert status == 200
        assert adapter.calls == []
        assert requests[0]["model"] == "chosen"
    finally:
        await router_runner.cleanup()
        await runner.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError("failed"), None])
async def test_router_failure_or_timeout_uses_explicit_default(failure: Exception | None) -> None:
    runner, upstream_url, requests = await _start_upstream("fallback")
    target = RouteTarget(upstream_url, "default", "default")
    adapter = FakeAdapter(failure or RouteDecision(target, (), {}), delay=0.1 if failure is None else 0)
    router_runner, router_url = await _start_router(
        [target],
        adapter,
        fallback=RouteDecision(target, (), {}, "fallback_default"),
        router_timeout=0.01 if failure is None else None,
    )
    try:
        status, _ = await _post(router_url, {"messages": [{"role": "user", "content": "hello"}]})
        assert status == 200
        assert requests[0]["model"] == "default"
    finally:
        await router_runner.cleanup()
        await runner.cleanup()


@pytest.mark.anyio
async def test_ai_router_rejects_unknown_default_addr() -> None:
    router = AiRouter(
        session_socket="http://127.0.0.1:8100",
        router_model="router-small",
        router_base_url="https://router.example/v1",
        upstream=['{"addr":"http://127.0.0.1:7001","model_name":"qwen-plus","description":"General"}'],
        default_addr="127.0.0.1:9999",
    )

    with pytest.raises(ValueError, match="--default-addr"):
        await router.run()


@pytest.mark.anyio
async def test_missing_user_context_uses_first_fallback_without_calling_adapter() -> None:
    runner, upstream_url, requests = await _start_upstream("first")
    target = RouteTarget(upstream_url, "first", "first")
    adapter = FakeAdapter(RuntimeError("must not run"))
    router_runner, router_url = await _start_router(
        [target], adapter, fallback=RouteDecision(target, (), {}, "fallback_first")
    )
    try:
        status, _ = await _post(router_url, {"messages": [{"role": "assistant", "content": "hi"}]})
        assert status == 200
        assert adapter.calls == []
        assert requests[0]["model"] == "first"
    finally:
        await router_runner.cleanup()
        await runner.cleanup()


@pytest.mark.anyio
async def test_router_logs_votes_in_selection_message(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, upstream_url, _requests = await _start_upstream("logged")
    target = RouteTarget(upstream_url, "logged-model", "logged")
    decision = RouteDecision(target, (("reason", "logged-model"),), {"logged-model": 1})
    adapter = FakeAdapter(decision)
    router_runner, router_url = await _start_router(
        [target], adapter, fallback=RouteDecision(target, (), {}, "fallback_first")
    )
    messages: list[str] = []

    def fake_info(message: str) -> None:
        messages.append(message)

    monkeypatch.setattr(router_module.logger, "info", fake_info)
    try:
        status, _ = await _post(router_url, {"messages": [{"role": "user", "content": "hello"}]})
        assert status == 200
        assert any("votes={'logged-model': 1}" in message for message in messages)
    finally:
        await router_runner.cleanup()
        await runner.cleanup()
