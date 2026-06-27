from __future__ import annotations

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._socket import serve_app


LOCK_KEY: web.AppKey[anyio.Lock] = web.AppKey("psi_agent.session.server.lock")


async def serve_session(
    *,
    channel_socket: str,
    handler: Handler,
    lock: anyio.Lock,
) -> None:
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app[LOCK_KEY] = lock
    app.router.add_post("/chat/completions", handler)

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await serve_app(app, channel_socket)
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
