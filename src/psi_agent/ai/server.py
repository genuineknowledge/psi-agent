from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

from psi_agent._trace import TRACE_ID_HEADER, resolve_trace_id


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    try:
        body: dict[str, Any] = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object")
        trace_id = resolve_trace_id(headers=request.headers, routing=body.get("routing"))
        logger.info(f"Received chat completion request trace_id={trace_id}")
        logger.debug(f"Request body trace_id={trace_id}: {json.dumps(body, ensure_ascii=False)[:1000]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e!r}")
        # OpenAI-compatible error response.
        return web.json_response(
            {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
            status=400,
        )

    provider = request.app["provider"]
    model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]

    logger.debug(f"Body keys before pop: {list(body)}")
    messages = body.pop("messages", [])
    body.pop("stream", None)
    body.pop("provider", None)
    body.pop("model", None)
    body.pop("api_key", None)
    body.pop("api_base", None)
    body.pop("routing", None)
    stream_opts = body.get("stream_options", {})
    if isinstance(stream_opts, dict):
        stream_opts["include_usage"] = True
        body["stream_options"] = stream_opts
    else:
        body["stream_options"] = {"include_usage": True}
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
            TRACE_ID_HEADER: trace_id,
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
    compaction_needed = False
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
        logger.debug(f"Starting to consume upstream SSE stream trace_id={trace_id}")
        max_context_tokens: int = request.app.get("max_context_tokens", 0)
        compaction_usage: dict[str, int] = {}
        async for chunk in stream:
            if max_context_tokens > 0 and chunk.usage and chunk.usage.prompt_tokens > max_context_tokens:
                compaction_needed = True
                compaction_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
                logger.debug(
                    f"Compaction needed trace_id={trace_id}: "
                    f"prompt_tokens={chunk.usage.prompt_tokens} > threshold={max_context_tokens}"
                )
            data = chunk.model_dump_json()
            logger.debug(f"SSE chunk trace_id={trace_id}: {data[:1000]}")
            await response.write(f"data: {data}\n\n".encode())
        if compaction_needed:
            signal = json.dumps(
                make_compaction_signal(
                    prompt_tokens=compaction_usage.get("prompt_tokens", 0),
                    threshold=max_context_tokens,
                )
            )
            logger.debug(f"SSE compaction signal trace_id={trace_id}: {signal[:500]}")
            await response.write(f"data: {signal}\n\n".encode())
    except ConnectionResetError:
        # Downstream client (session/channel) disconnected — e.g. user pressed
        # "stop". The finally block closes the upstream provider stream.
        client_gone = True
        logger.info(f"Client disconnected; cancelling upstream stream trace_id={trace_id}")
    except Exception as e:
        upstream_error = True
        logger.error(
            f"Error forwarding to upstream trace_id={trace_id} (provider={provider!r}, model={model!r}): {e!r}"
        )
        err_chunk = json.dumps(
            {
                "id": "error",
                "choices": [{"index": 0, "delta": {"content": f"[Upstream Error]: {e}"}, "finish_reason": "error"}],
            }
        )
        logger.debug(f"SSE error chunk trace_id={trace_id}: {err_chunk[:1000]}")
        try:
            await response.write(f"data: {err_chunk}\n\n".encode())
        except Exception:
            logger.warning(f"Failed to send upstream error chunk to client trace_id={trace_id}")
    else:
        if compaction_needed:
            logger.debug(f"Request completed with compaction signal trace_id={trace_id}")
        else:
            logger.debug(f"Upstream stream completed successfully trace_id={trace_id}")
    finally:
        # Always release the upstream connection, even on cancellation
        # (client disconnect / shutdown). Shielded so aclose() completes
        # while a CancelledError is propagating through this finally.
        if stream is not None:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                logger.debug(f"Closing upstream stream trace_id={trace_id}")
                with anyio.CancelScope(shield=True):
                    try:
                        await aclose()
                    except Exception as close_err:
                        logger.warning(f"Failed to close upstream stream trace_id={trace_id}: {close_err}")

    if client_gone:
        logger.info(f"Request cancelled by client disconnect trace_id={trace_id}")
    elif upstream_error:
        logger.info(f"Request completed with upstream error trace_id={trace_id}")
    else:
        logger.info(f"Request completed successfully trace_id={trace_id}")
    return response
