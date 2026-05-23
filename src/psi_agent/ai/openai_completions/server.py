from __future__ import annotations

import json

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from loguru import logger

from psi_agent._protocol import ErrorResponse


async def serve_openai_completions(
    *,
    socket_path: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    logger.info(f"Starting openai-completions AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    logger.info(f"openai-completions listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down openai-completions on {socket_path}")
        await runner.cleanup()


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
            ClientSession(connector=TCPConnector(ssl=use_ssl), timeout=ClientTimeout(total=None)) as session,
            session.post(upstream_url, json=body, headers=headers) as upstream_resp,
        ):
            logger.info(f"Upstream response status: {upstream_resp.status}")
            if upstream_resp.status != 200:
                error_text = await upstream_resp.text()
                logger.error(f"Upstream error: {error_text[:500]}")
                err_chunk = json.dumps(
                    {
                        "id": "error",
                        "model": "",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": f"[Upstream Error {upstream_resp.status}]: {error_text[:300]}"},
                                "finish_reason": "error",
                            }
                        ],
                    }
                )
                await response.write(f"data: {err_chunk}\n\n".encode())
                return response

            async for raw_line in upstream_resp.content:
                line = raw_line.decode().strip()
                if line:
                    logger.debug(f"Upstream chunk: {line[:200]}")
                    await response.write((line + "\n\n").encode())
    except Exception as e:
        logger.error(f"Error forwarding to upstream: {e}")
        err_chunk = json.dumps(
            {
                "id": "error",
                "model": "",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"[Upstream Connection Error]: {e}"},
                        "finish_reason": "error",
                    }
                ],
            }
        )
        await response.write(f"data: {err_chunk}\n\n".encode())
        return response

    logger.debug("Request completed")
    return response
