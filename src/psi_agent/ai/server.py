from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

from ._keys import API_KEY_KEY, BASE_URL_KEY, MODEL_KEY, PROVIDER_KEY


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    trace_id = request.headers.get("X-Trace-ID")
    with logger.contextualize(trace_id=trace_id):
        return await _handle_chat_completions(request)


async def _handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body: dict[str, Any] = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:1000]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e!r}")
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
            "X-Accel-Buffering": "no",
        },
    )
    try:
        await response.prepare(request)
    except Exception:
        logger.warning("Client disconnected before SSE response prepared")
        return response

    logger.debug(f"Forwarding to upstream: provider={provider!r}, model={model!r}, base_url={base_url!r}")
    upstream_error = False
    client_gone = False
    stream: AsyncIterator[ChatCompletionChunk] | None = None

    max_retries = 3
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
                wait = 0.5 * (2**attempt)
                logger.warning(f"Upstream call failed (attempt {attempt + 1}): {e!r}. Retrying in {wait}s...")
                await anyio.sleep(wait)
            else:
                logger.error(f"Upstream call failed after {max_retries} attempts: {e!r}")
                err_chunk = json.dumps(
                    {
                        "id": "error",
                        "choices": [
                            {"index": 0, "delta": {"content": f"[Upstream Error]: {e}"}, "finish_reason": "error"}
                        ],
                    }
                )
                with suppress(Exception):
                    await response.write(f"data: {err_chunk}\n\n".encode())
                return response

    try:
        logger.debug("Starting to consume upstream SSE stream")
        assert stream is not None
        async for chunk in stream:
            data = chunk.model_dump_json()
            logger.debug(f"SSE chunk: {data[:1000]}")
            await response.write(f"data: {data}\n\n".encode())
    except ConnectionResetError:
        # Downstream client (session/channel) disconnected — e.g. user pressed
        # "stop". The finally block closes the upstream provider stream.
        client_gone = True
        logger.info("Client disconnected; cancelling upstream stream")
    except Exception as e:
        upstream_error = True
        logger.exception(f"Error forwarding to upstream (provider={provider!r}, model={model!r}): {e!r}")
        err_chunk = json.dumps(
            {
                "id": "error",
                "choices": [{"index": 0, "delta": {"content": f"[Upstream Error]: {e}"}, "finish_reason": "error"}],
            }
        )
        logger.debug(f"SSE error chunk: {err_chunk[:1000]}")
        try:
            await response.write(f"data: {err_chunk}\n\n".encode())
        except Exception:
            logger.warning("Failed to send upstream error chunk to client")
    else:
        logger.debug("Upstream stream completed successfully")
    finally:
        # Always release the upstream connection, even on cancellation
        # (client disconnect / shutdown). Shielded so aclose() completes
        # while a CancelledError is propagating through this finally.
        if stream is not None:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                logger.debug("Closing upstream stream")
                with anyio.CancelScope(shield=True):
                    try:
                        await aclose()
                    except Exception as close_err:
                        logger.warning(f"Failed to close upstream stream: {close_err}")

    if client_gone:
        logger.info("Request cancelled by client disconnect")
    elif upstream_error:
        logger.info("Request completed with upstream error")
    else:
        logger.info("Request completed successfully")
    return response
