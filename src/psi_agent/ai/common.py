"""Shared utilities for AI backends."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import anyio
from aiohttp import web
from loguru import logger


@dataclass
class ErrorResponse:
    message: str
    type: str
    code: str

    def to_dict(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class SSEChunk:
    """A typed SSE chunk for OpenAI Chat Completions format.

    Use to_sse() to get the complete 'data: {...}\n\n' string.
    """

    delta_content: str = ""
    delta_reasoning: str = ""
    delta_tool_calls: list[dict] | None = None
    finish_reason: str | None = None
    chunk_id: str = ""

    def to_sse(self) -> str:
        delta: dict = {}
        if self.delta_content:
            delta["content"] = self.delta_content
        if self.delta_reasoning:
            delta["reasoning_content"] = self.delta_reasoning
        if self.delta_tool_calls is not None:
            delta["tool_calls"] = self.delta_tool_calls
        return f"data: {
            json.dumps(
                {
                    'id': self.chunk_id or 'chatcmpl-unknown',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': self.finish_reason}],
                },
                ensure_ascii=False,
            )
        }\n\n"


async def serve_ai_backend(
    *,
    socket_path: str,
    model: str,
    api_key: str,
    base_url: str,
    name: str,
    handler: Callable[[web.Request], Coroutine[Any, Any, web.StreamResponse]],
) -> None:
    """Serve an AI backend on a Unix socket or local TCP URL."""

    logger.info(f"Starting {name} AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/v1/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = _make_site(runner, socket_path)
    await site.start()

    logger.info(f"{name} listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down {name} on {socket_path}")
        await runner.cleanup()


def _make_site(runner: web.AppRunner, socket_path: str) -> web.BaseSite:
    if socket_path.startswith("http://"):
        parsed = urlparse(socket_path)
        if not parsed.hostname or parsed.port is None:
            raise ValueError(f"TCP AI backend URL must include host and port: {socket_path}")
        return web.TCPSite(runner, parsed.hostname, parsed.port)
    return web.UnixSite(runner, socket_path)
