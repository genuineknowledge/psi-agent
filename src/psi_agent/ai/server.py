from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

from psi_agent.ai import API_KEY_KEY, BASE_URL_KEY, MODEL_KEY, PROVIDER_KEY


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    trace_id = request.headers.get("X-Trace-ID", uuid.uuid4().hex)
    with logger.contextualize(trace_id=trace_id):
        return await _handle_chat_completions(request)


async def _handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body: dict[str, Any] = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:1000]}")
    except Exception as e:
        logger.exception(f"Failed to parse request body: {e}")
        # OpenAI-compatible error response.
        return web.json_response(
            {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
            status=400,
        )

    provider = request.app[PROVIDER_KEY]
    model = request.app[MODEL_KEY]
    api_key = request.app[API_KEY_KEY]
    base_url = request.app[BASE_URL_KEY]

    logger.debug(f"Body keys before pop: {list(body)}")
    messages = body.pop("messages", [])
    body.pop("stream", None)
    body.pop("provider", None)
    body.pop("model", None)
    body.pop("api_key", None)
    body.pop("api_base", None)
    logger.debug(f"Body keys to passthrough: {list(body)}")

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            # SSE standard headers — per MDN / HTML spec
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    try:
        await response.prepare(request)
    except Exception:
        logger.exception("Failed to prepare SSE response, client likely disconnected")
        return response

    # Exponential backoff retry logic for upstream AI calls.
    # Only retry if an error occurs before streaming starts (response.prepare).
    max_retries = 3
    retry_delay = 1.0  # seconds
    stream: AsyncIterator[ChatCompletionChunk] | None = None

    for attempt in range(max_retries):
        try:
            stream = cast(
                AsyncIterator[ChatCompletionChunk],
                # ``acompletion()`` returns ``ChatCompletion | AsyncIterator[ChatCompletionChunk]``
                # depending on the ``stream`` flag.  We always pass ``stream=True``, so the
                # runtime type is always ``AsyncIterator[ChatCompletionChunk]`` — the cast is safe.
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
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = retry_delay * (2**attempt)
                logger.warning(f"Upstream AI call failed (attempt {attempt + 1}): {e}. Retrying in {wait}s...")
                await anyio.sleep(wait)
            else:
                logger.error(f"Upstream AI call failed after {max_retries} attempts: {e}")
                err_chunk = json.dumps(
                    {
                        "id": "error",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": f"[Upstream Error]: {e}"},
                                "finish_reason": "error",
                            }
                        ],
                    }
                )
                try:
                    await response.write(f"data: {err_chunk}\n\n".encode())
                except Exception:
                    logger.warning("Failed to write error chunk to SSE stream")
                return response

    if stream is not None:
        try:
            logger.debug("Starting to consume upstream SSE stream")
            async for chunk in stream:
                data = chunk.model_dump_json()
                logger.debug(f"SSE chunk: {data[:1000]}")
                await response.write(f"data: {data}\n\n".encode())
        except Exception as e:
            logger.exception(f"Error forwarding to upstream: {e}")
            # OpenAI ChatCompletionChunk format with ``finish_reason="error"``
            # — a psi-agent extension for layer-to-layer SSE error signalling.
            err_chunk = json.dumps(
                {
                    "id": "error",
                    "choices": [{"index": 0, "delta": {"content": f"[Upstream Error]: {e}"}, "finish_reason": "error"}],
                }
            )
            try:
                await response.write(f"data: {err_chunk}\n\n".encode())
            except Exception:
                logger.warning("Failed to write error chunk to SSE stream")

    logger.info("Request completed")
    return response
