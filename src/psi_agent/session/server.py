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
    """Create an aiohttp server that routes channel traffic to the agent.

    - ``POST /chat/completions`` → ``agent.handle_request`` (chat SSE)
    - ``POST /events`` → ``agent.handle_event`` (normalized event envelopes)
    """
    logger.info(f"Starting session server on {channel_socket}")

    # Large conversation contexts (long histories, tool outputs) routinely exceed
    # aiohttp's 1 MiB default body limit, which would reject the request with
    # HTTPRequestEntityTooLarge before it reaches the agent. Match the gateway
    # and AI-forwarder apps' 100 MiB ceiling so the same payloads flow through.
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app.router.add_post("/chat/completions", agent.handle_request)
    app.router.add_post("/events", agent.handle_event)

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
