"""Shared utilities for AI backends."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

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


async def serve_ai_backend(
    *,
    socket_path: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    name: str,
    handler: Callable[[web.Request], Coroutine[Any, Any, web.StreamResponse]],
) -> None:
    """Serve an AI backend on a Unix socket with shared scaffolding."""

    logger.info(f"Starting {name} AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app["provider"] = provider
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/v1/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    logger.info(f"{name} listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down {name} on {socket_path}")
        await runner.cleanup()
