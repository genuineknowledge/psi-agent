from __future__ import annotations

import json
from urllib.parse import urlparse

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, ErrorResponse, StreamChoice


def _build_site(runner: web.AppRunner, socket_path: str) -> web.BaseSite:
    """Resolve a listen address into the matching aiohttp site.

    Same three address forms as the AI side: TCP URL, Windows named pipe, or
    Unix domain socket path. Lets a channel reach the session over any of them.
    """
    if socket_path.startswith(("http://", "https://")):
        parsed = urlparse(socket_path)
        return web.TCPSite(runner, host=parsed.hostname or "127.0.0.1", port=parsed.port or 8000)
    if socket_path.startswith("npipe://") or socket_path.startswith("\\\\.\\pipe\\"):
        return web.NamedPipeSite(runner, socket_path.removeprefix("npipe://"))
    return web.UnixSite(runner, socket_path)


async def serve_session(
    *,
    channel_socket: str,
    agent: SessionAgent,
    lock: anyio.Lock,
) -> None:
    logger.info(f"Starting session server on {channel_socket}")

    app = web.Application()
    app["agent"] = agent
    app["lock"] = lock
    app.router.add_post("/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    site = _build_site(runner, channel_socket)
    await site.start()

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        await runner.cleanup()


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received channel request")
    agent: SessionAgent = request.app["agent"]
    lock: anyio.Lock = request.app["lock"]

    try:
        body = await request.json()
        logger.debug(f"Channel request body: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        err = ErrorResponse(message=str(e), type="invalid_request", code="400")
        return web.json_response(err.to_dict(), status=400)

    messages = body.get("messages", [])
    if not messages:
        err = ErrorResponse(message="No messages in request", type="invalid_request", code="400")
        return web.json_response(err.to_dict(), status=400)

    # Channel only sends the latest message, not history
    user_message = messages[-1]
    if user_message.get("role") != "user":
        user_message = {"role": "user", "content": str(user_message.get("content", ""))}

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    async with lock:
        await response.prepare(request)
        logger.info("Acquired session lock, processing request")
        try:
            async for chunk in agent.run(user_message):
                await response.write(chunk.to_sse().encode())
                logger.debug(
                    f"Chunk sent: content={chunk.choices[0].delta.content!r}, "
                    f"reasoning={chunk.choices[0].delta.reasoning_content!r}"
                )
        except Exception as e:
            logger.error(f"Error in agent run: {e}")
            err_chunk = ChatCompletionChunk(
                id="error",
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaMessage(content=f"[Session Error: {e}]"),
                        finish_reason="stop",
                    )
                ],
            )
            await response.write(err_chunk.to_sse().encode())

    await response.write(b"data: [DONE]\n\n")
    logger.debug("Session request completed")
    return response
