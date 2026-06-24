from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import cast

from aiohttp import web
from any_llm.api import ChatCompletionChunk as AnyLlmChunk
from any_llm.api import acompletion
from loguru import logger

from psi_agent.session.protocol import ChatCompletionChunk as ProtocolChunk

from . import APP_API_KEY, APP_BASE_URL, APP_MODEL, APP_PROVIDER


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    trace_id = request.headers.get("X-Trace-ID", uuid.uuid4().hex[:8])
    with logger.contextualize(trace_id=trace_id):
        return await _handle_chat_completions(request)


async def _handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body: dict = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        return web.json_response(
            {"error": {"message": str(e), "type": "invalid_request", "code": "400"}},
            status=400,
        )

    provider = request.app[APP_PROVIDER]
    model = request.app[APP_MODEL]
    api_key = request.app[APP_API_KEY]
    base_url = request.app[APP_BASE_URL]

    messages = body.pop("messages")
    body.pop("model", None)
    body.pop("stream", None)

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    try:
        # Forward to upstream LLM with a 60s total timeout for the initial response.
        stream = cast(
            AsyncIterator[AnyLlmChunk],
            await acompletion(
                provider=provider,
                model=model,
                messages=messages,
                stream=True,
                api_key=api_key,
                api_base=base_url,
                timeout=60,
                **body,
            ),
        )
        async for chunk in stream:
            data = chunk.model_dump_json()
            logger.debug(f"SSE chunk: {data[:200]}")
            await response.write(f"data: {data}\n\n".encode())
    except Exception as e:
        logger.exception(f"Error forwarding to upstream: {e}")
        err_chunk = ProtocolChunk.error(f"[Upstream Error]: {e}")
        await response.write(err_chunk.to_sse().encode())

    logger.info("Request completed")
    return response
