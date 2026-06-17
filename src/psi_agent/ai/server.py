from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger


@dataclass
class ErrorResponse:
    message: str
    type: str
    code: str

    def to_dict(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body: dict = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        err = ErrorResponse(message=str(e), type="invalid_request", code="400")
        return web.json_response(err.to_dict(), status=400)

    provider = request.app["provider"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]
    startup_model = request.app["model"]

    body["model"] = startup_model or body.get("model", "")

    messages = body.pop("messages")
    tools = body.pop("tools", None)
    body.pop("stream", None)  # handled explicitly below

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
                model=body.pop("model"),
                messages=messages,
                tools=tools,
                stream=True,
                api_key=api_key,
                api_base=base_url,
                **body,
            ),
        )
        async for chunk in stream:
            data = chunk.model_dump_json()
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

    logger.debug("Request completed")
    return response
