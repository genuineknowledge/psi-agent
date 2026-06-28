from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._sockets import create_site

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


async def serve_session(*, channel_socket: str, agent: SessionAgent) -> None:
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)

    runner = web.AppRunner(app)
    await runner.setup()
    site = create_site(runner, channel_socket)
    await site.start()

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        await runner.cleanup()
