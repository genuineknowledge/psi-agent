from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp import ClientSession, web

from psi_agent._trace import TRACE_ID_HEADER
from psi_agent.gateway._chat_manager import ChatManager
from psi_agent.gateway.server import _handle_chat
from psi_agent.router.client import RouterHttpClient
from psi_agent.router.fallback import FallbackConfig, FallbackStrategy
from psi_agent.router.models import RouterTarget
from psi_agent.router.server import create_router_app
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.tool_registry import ToolRegistry

TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


@asynccontextmanager
async def _serve(app: web.Application) -> AsyncIterator[str]:
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = cast(Any, site._server).sockets if site._server is not None else []
    assert sockets
    try:
        yield f"http://127.0.0.1:{sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


def _sse_events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


@pytest.mark.anyio
async def test_spa_gateway_session_router_target_share_one_trace_id(tmp_path: Path) -> None:
    target_observation: dict[str, Any] = {}

    async def target_handler(request: web.Request) -> web.StreamResponse:
        target_observation["trace_id"] = request.headers.get(TRACE_ID_HEADER)
        target_observation["body"] = await request.json()
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                TRACE_ID_HEADER: request.headers[TRACE_ID_HEADER],
            }
        )
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"index":0,"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n')
        await response.write(b"data: [DONE]\n\n")
        return response

    target_app = web.Application()
    target_app.router.add_post("/chat/completions", target_handler)
    async with _serve(target_app) as target_url:
        strategy = FallbackStrategy(
            config=FallbackConfig(
                session_socket="unused",
                targets=[RouterTarget("candidate-1", target_url, "target")],
            ),
            client=RouterHttpClient(),
        )
        async with _serve(create_router_app(strategy=strategy)) as router_url:
            agent = SessionAgent(
                ai_client=AiClient(router_url),
                conversation=Conversation(path=tmp_path / "histories" / "trace-session.jsonl"),
                tool_registry=ToolRegistry(),
            )
            session_app = web.Application()
            session_app.router.add_post("/chat/completions", agent.handle_request)
            async with _serve(session_app) as session_url:

                class SessionManager:
                    @staticmethod
                    def get_socket(session_id: str) -> str:
                        assert session_id == "session-1"
                        return session_url

                gateway_app = web.Application()
                gateway_app["sm"] = SessionManager()
                gateway_app["cm"] = ChatManager()
                gateway_app.router.add_post("/sessions/{session_id}/chat", _handle_chat)
                async with _serve(gateway_app) as gateway_url, ClientSession() as client:
                    response = await client.post(
                        f"{gateway_url}/sessions/session-1/chat",
                        json={"chunks": [{"type": "text", "text": "hello"}]},
                        headers={TRACE_ID_HEADER: TRACE_ID},
                    )
                    response_text = await response.text()

    events = _sse_events(response_text)
    assert response.status == 200
    assert response.headers[TRACE_ID_HEADER] == TRACE_ID
    assert events
    assert all(event["trace_id"] == TRACE_ID for event in events)
    assert [event["type"] for event in events] == ["router_status", "router_status", "text"]
    assert target_observation["trace_id"] == TRACE_ID
    assert "routing" not in target_observation["body"]
