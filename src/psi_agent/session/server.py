"""aiohttp server that binds ``agent.handle_request`` to the channel socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._sockets import create_site

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


async def serve_session(*, channel_socket: str, agent: SessionAgent) -> None:
    """Create an aiohttp server that routes ``POST /chat/completions`` to
    ``agent.handle_request``.

    Startup failures are caught, the runner is cleaned up under a shielded
    cancel scope, then the exception is re-raised.  Normal shutdown in
    the ``finally`` block is likewise shielded.
    """
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app.router.add_post("/chat/completions", agent.handle_request)

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, channel_socket)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start session server on {channel_socket}: {e}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
