from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
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

    provider = request.app["provider"]
    model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]

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
        stream = cast(
            AsyncIterator[ChatCompletionChunk],
            await acompletion(
                provider=provider,
                model=model,
                messages=messages,
                stream=True,
                api_key=api_key,
                api_base=base_url,
                **body,
            ),
        )
        async for chunk in stream:
            data = chunk.model_dump_json()
            logger.debug(f"SSE chunk: {data[:200]}")
            await response.write(f"data: {data}\n\n".encode())
    except Exception as e:
        logger.error(f"Error forwarding to upstream: {e}")
        err_chunk = json.dumps(
            {
                "id": "error",
                "model": "",
                "choices": [{"index": 0, "delta": {"content": f"[Upstream Error]: {e}"}, "finish_reason": "error"}],
            }
        )
        await response.write(f"data: {err_chunk}\n\n".encode())

    logger.info("Request completed")
    return response
