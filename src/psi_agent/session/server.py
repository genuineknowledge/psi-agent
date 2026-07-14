"""aiohttp server that binds ``agent.handle_request`` to the channel socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

from psi_agent import _keys
from psi_agent._sockets import serve_app, trace_middleware

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


async def serve_session(*, channel_socket: str, agent: SessionAgent) -> None:
    """Create an aiohttp server that routes ``POST /chat/completions`` to
    ``agent.handle_request``.
    """
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application(middlewares=[trace_middleware])
    app[_keys.AGENT] = agent
    app.router.add_post("/chat/completions", agent.handle_request)

    await serve_app(app, channel_socket)
