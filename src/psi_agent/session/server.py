"""aiohttp server that binds ``agent.handle_request`` to the channel socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

from psi_agent._sockets import serve_app

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

    await serve_app(app, channel_socket)
