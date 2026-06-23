from __future__ import annotations

import json

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.channel.session_client import stream_session_reply
from psi_agent.errors import UserFacingError
from psi_agent.net import make_server_site

from .page import INDEX_HTML

SESSION_SOCKET_KEY = web.AppKey("session_socket", str)


async def handle_index(request: web.Request) -> web.StreamResponse:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def handle_chat(request: web.Request) -> web.StreamResponse:
    """Proxy one user message to the session and stream the reply back as SSE.

    Mirrors what the REPL does: forward the message to the session socket,
    then relay `content` (and `reasoning_content`) deltas to the browser.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)

    message = str(body.get("message", "")).strip()
    if not message:
        return web.json_response({"error": "empty message"}, status=400)

    session_socket = request.app[SESSION_SOCKET_KEY]

    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    await resp.prepare(request)

    try:
        async for delta in stream_session_reply(session_socket=session_socket, message=message):
            payload: dict[str, str] = {}
            if delta.reasoning:
                payload["reasoning"] = delta.reasoning
            if delta.content:
                payload["content"] = delta.content
            if payload:
                await resp.write(f"data: {json.dumps(payload)}\n\n".encode())
    except UserFacingError as e:
        await resp.write(f"data: {json.dumps({'error': str(e)})}\n\n".encode())
    except Exception as e:
        logger.exception("Web channel chat error")
        await resp.write(f"data: {json.dumps({'error': f'Unexpected error: {e}'})}\n\n".encode())

    await resp.write(b"data: [DONE]\n\n")
    return resp


async def serve_web_channel(*, session_socket: str, listen: str) -> None:
    app = web.Application()
    app[SESSION_SOCKET_KEY] = session_socket
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/chat", handle_chat)

    runner = web.AppRunner(app)
    await runner.setup()
    site = await make_server_site(runner, listen)
    await site.start()

    logger.info(f"Web channel listening on {listen}")
    logger.warning("Web channel has no authentication. Bind to 127.0.0.1 or put it behind a trusted proxy.")

    try:
        await anyio.sleep_forever()
    finally:
        await runner.cleanup()
