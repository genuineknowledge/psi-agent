from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Final, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

APP_PROVIDER: Final = web.AppKey("provider", str)
APP_MODEL: Final = web.AppKey("model", str)
APP_API_KEY: Final = web.AppKey("api_key", str)
APP_BASE_URL: Final = web.AppKey("base_url", str)


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    trace_id = request.headers.get("X-Trace-ID", uuid.uuid4().hex)
    with logger.contextualize(trace_id=trace_id):
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
                "X-Trace-ID": trace_id,
            },
        )
        await response.prepare(request)

        # Retry logic for upstream AI requests
        max_retries = 3
        retry_delay = 1.0
        stream = None

        for attempt in range(max_retries):
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
                break  # Success
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Upstream request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying...")
                    await anyio.sleep(retry_delay * (2**attempt))
                else:
                    logger.exception(f"Upstream request failed after {max_retries} attempts")
                    err_chunk = json.dumps(
                        {
                            "id": "error",
                            "model": "",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": f"[Upstream Error after {max_retries} attempts]: {e}"},
                                    "finish_reason": "error",
                                }
                            ],
                        }
                    )
                    await response.write(f"data: {err_chunk}\n\n".encode())
                    return response

        if stream:
            try:
                async for chunk in stream:
                    data = chunk.model_dump_json()
                    logger.debug(f"SSE chunk: {data[:200]}")
                    await response.write(f"data: {data}\n\n".encode())
            except Exception as e:
                logger.exception(f"Error during stream consumption: {e}")
                err_chunk = json.dumps(
                    {
                        "id": "error",
                        "model": "",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": f"[Upstream Stream Error]: {e}"},
                                "finish_reason": "error",
                            }
                        ],
                    }
                )
                await response.write(f"data: {err_chunk}\n\n".encode())

        logger.info("Request completed")
        return response
