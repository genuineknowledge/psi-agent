from __future__ import annotations

import json

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.net import cleanup_endpoint_sidecar, make_server_site
from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, ErrorResponse, StreamChoice


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
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    site = await make_server_site(runner, channel_socket)
    await site.start()

    logger.info(f"Session server listening on {channel_socket}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        await runner.cleanup()
        cleanup_endpoint_sidecar(channel_socket)


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

    # Spawn after_turn background task outside the lock so it doesn't block
    # the next request. Must be created here (not inside the async generator)
    # to guarantee the event loop has a chance to schedule it.
    agent.spawn_after_turn_task()

    await response.write(b"data: [DONE]\n\n")
    logger.debug("Session request completed")
    return response
