from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger


def _resolve_model(body: dict[str, Any], default_model: str) -> tuple[str, str | None]:
    """Resolve the upstream model for one request.

    The request body can optionally override the startup default.  The
    caller is responsible for mutating the body before forwarding it to
    ``any_llm.acompletion()``.
    """
    request_model = body.pop("model", None)
    body.pop("stream", None)
    return request_model or default_model, request_model


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
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

    provider = request.app["provider"]
    default_model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]

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
        logger.debug("Starting to consume upstream SSE stream")
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
        logger.error(f"Error forwarding to upstream (provider={provider!r}, model={model!r}): {e!r}")
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
