from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from psi_agent.router import client as client_module
from psi_agent.router.client import RouterHttpClient
from psi_agent.router.errors import RouterUpstreamError


class EventClient(RouterHttpClient):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]:
        del socket, body, options

        async def generate() -> AsyncGenerator[dict[str, Any]]:
            for event in self.events:
                yield event

        return generate()


@pytest.mark.anyio
async def test_complete_accumulates_streamed_content_reasoning_and_tool_calls() -> None:
    client = EventClient(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "content": "hel",
                            "reasoning": "why ",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": '{"q":'},
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "content": "lo",
                            "reasoning": "now",
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '"x"}'}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
    )

    result = await client.complete(socket="unused", body={}, timeout=1.0)

    assert result.content == "hello"
    assert result.reasoning == "why now"
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }
    ]


@pytest.mark.anyio
async def test_compaction_signal_does_not_replace_normal_finish_reason() -> None:
    client = EventClient(
        [
            {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]},
            {
                "choices": [{"delta": {}, "finish_reason": "compaction_needed"}],
                "psi_compaction": {"needed": True, "prompt_tokens": 10, "threshold": 9},
            },
        ]
    )

    result = await client.complete(socket="unused", body={}, timeout=None)

    assert result.content == "done"
    assert result.finish_reason == "stop"


@pytest.mark.anyio
async def test_complete_rejects_compaction_without_normal_finish_reason() -> None:
    client = EventClient(
        [
            {
                "choices": [{"delta": {}, "finish_reason": "compaction_needed"}],
                "psi_compaction": {"needed": True, "prompt_tokens": 10, "threshold": 9},
            }
        ]
    )

    with pytest.raises(RouterUpstreamError, match="without a finish reason"):
        await client.complete(socket="unused", body={}, timeout=None)


@pytest.mark.anyio
async def test_stream_rejects_compaction_without_normal_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        event = {
            "choices": [{"delta": {}, "finish_reason": "compaction_needed"}],
            "psi_compaction": {"needed": True, "prompt_tokens": 10, "threshold": 9},
        }
        await response.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    server = TestServer(app)
    await server.start_server()

    def resolve(_: str) -> tuple[aiohttp.BaseConnector, str]:
        return aiohttp.TCPConnector(), str(server.make_url("/chat/completions"))

    monkeypatch.setattr(client_module, "resolve_connector_and_endpoint", resolve)
    client = RouterHttpClient()
    try:
        with pytest.raises(RouterUpstreamError, match="without a completion finish reason"):
            async for _event in client.stream(socket="unused", body={}, timeout=None):
                pass
    finally:
        await server.close()
