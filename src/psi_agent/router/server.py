from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import aiohttp
from aiohttp import ClientTimeout, web
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint

from .models import Upstream
from .selector import RouterSelectionError, select_upstream, serialize_context


@dataclass(frozen=True)
class RouterSettings:
    targets: tuple[Upstream, ...]
    router_model: str
    router_base_url: str
    router_api_key: str
    default_socket: str
    router_timeout: float | None
    context_chars: int
    log_details: bool


ROUTER_SETTINGS_KEY = web.AppKey("router_settings", RouterSettings)


def _error_payload(message: str, code: int) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "router_error",
            "param": None,
            "code": code,
        }
    }


def _error_chunk(message: str) -> bytes:
    payload = {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": f"[Router Error]: {message}"},
                "finish_reason": "error",
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _proxy_request(request: web.Request, *, body: dict[str, Any], socket: str) -> web.StreamResponse:
    connector, endpoint = resolve_connector_and_endpoint(socket)
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    try:
        async with (
            aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None)) as session,
            session.post(endpoint, json=body) as upstream_response,
        ):
            if upstream_response.status != 200:
                detail = (await upstream_response.text())[:1000]
                logger.warning(f"Router upstream {socket!r} returned HTTP {upstream_response.status}: {detail!r}")
                return web.json_response(
                    _error_payload(detail or f"Upstream returned HTTP {upstream_response.status}", 502),
                    status=502,
                )
            await response.prepare(request)
            async for raw in upstream_response.content.iter_any():
                logger.debug(f"Router SSE chunk: {raw!r}")
                await response.write(raw)
            await response.write_eof()
    except ConnectionResetError:
        logger.info("Router client disconnected; cancelling upstream proxy")
    except Exception as exc:
        logger.error(f"Router proxy error for upstream {socket!r}: {exc!r}")
        if response.prepared:
            with suppress(Exception):
                await response.write(_error_chunk(str(exc)))
            with suppress(Exception):
                await response.write_eof()
            return response
        return web.json_response(_error_payload(str(exc), 502), status=502)
    return response


async def handle_router_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received router chat completion request")
    try:
        payload: Any = await request.json()
    except Exception as exc:
        logger.error(f"Failed to parse router request body: {exc!r}")
        return web.json_response(_error_payload(str(exc), 400), status=400)
    if not isinstance(payload, dict):
        return web.json_response(_error_payload("Request body must be a JSON object", 400), status=400)

    settings = request.app[ROUTER_SETTINGS_KEY]
    body: dict[str, Any] = dict(payload)
    context = serialize_context(body.get("messages"), max_chars=settings.context_chars)
    socket = settings.default_socket
    reason = "No usable user context; using default address"
    if context:
        try:
            decision = await select_upstream(
                context=context,
                targets=settings.targets,
                router_model=settings.router_model,
                router_base_url=settings.router_base_url,
                router_api_key=settings.router_api_key,
                router_timeout=settings.router_timeout,
            )
            target = settings.targets[decision.candidate]
            socket = target.socket
            reason = decision.reason
        except RouterSelectionError as exc:
            reason = str(exc)
            logger.warning(f"Semantic routing failed; using default address: {exc}")
    if settings.log_details:
        logger.info(f"Router reason: {reason}")
    logger.info(f"Router result: socket={socket!r}")
    return await _proxy_request(request, body=body, socket=socket)
