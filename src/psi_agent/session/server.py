"""aiohttp server that binds ``agent.handle_request`` to the channel socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from psi_agent._sockets import serve_app, trace_id_middleware

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


async def serve_session(*, channel_socket: str, agent: SessionAgent) -> None:
    """Create an aiohttp server that routes ``POST /chat/completions`` to
    ``agent.handle_request``.
    """
    app = web.Application(middlewares=[trace_id_middleware])
    app.router.add_post("/chat/completions", agent.handle_request)

    await serve_app(app, channel_socket, name="Session")
