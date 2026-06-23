from __future__ import annotations

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger


async def serve_session(
    *,
    channel_socket: str,
    handler: Handler,
    lock: anyio.Lock,
) -> None:
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app["lock"] = lock
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, channel_socket)
    await site.start()

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        await runner.cleanup()
