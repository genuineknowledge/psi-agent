from __future__ import annotations

import json

from aiohttp import ClientSession, ClientTimeout, web
from loguru import logger

from psi_agent.ai.common import ErrorResponse, SSEChunk, serve_ai_backend, write_sse_bytes
from psi_agent.net import make_tcp_connector


async def serve_openai_completions(
    *,
    socket_path: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    await serve_ai_backend(
        socket_path=socket_path,
        model=model,
        api_key=api_key,
        base_url=base_url,
        name="openai-completions",
        handler=handle_chat_completions,
    )


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        err = ErrorResponse(message=str(e), type="invalid_request", code="400")
        return web.json_response(err.to_dict(), status=400)

    model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]

    upstream_url = base_url.rstrip("/") + "/chat/completions"

    body["model"] = model

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    logger.info(f"Forwarding to upstream: {upstream_url}")
    logger.debug("Upstream headers: Authorization=Bearer *** (api_key hidden)")

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
        use_ssl = base_url.startswith("https")
        async with (
            ClientSession(connector=make_tcp_connector(ssl=use_ssl), timeout=ClientTimeout(total=None)) as session,
            session.post(upstream_url, json=body, headers=headers) as upstream_resp,
        ):
            logger.info(f"Upstream response status: {upstream_resp.status}")
            if upstream_resp.status != 200:
                error_text = await upstream_resp.text()
                logger.error(f"Upstream error: {error_text[:500]}")
                chunk = SSEChunk(
                    delta_content=f"[Upstream Error {upstream_resp.status}]: {error_text[:300]}",
                    finish_reason="error",
                    chunk_id="error",
                )
                await write_sse_bytes(response, chunk.to_sse().encode())
                return response

            async for raw_line in upstream_resp.content:
                line = raw_line.decode().strip()
                if line:
                    logger.debug(f"Upstream chunk: {line[:200]}")
                    if not await write_sse_bytes(response, (line + "\n\n").encode()):
                        return response
    except ConnectionResetError, BrokenPipeError:
        logger.info("Downstream client disconnected while forwarding upstream response")
        return response
    except Exception as e:
        logger.error(f"Error forwarding to upstream: {e}")
        chunk = SSEChunk(
            delta_content=f"[Upstream Connection Error]: {e}",
            finish_reason="error",
            chunk_id="error",
        )
        await write_sse_bytes(response, chunk.to_sse().encode())
        return response

    logger.debug("Request completed")
    return response
