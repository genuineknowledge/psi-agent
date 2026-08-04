"""OpenAI-compatible HTTP/SSE boundary shared by Router strategies."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any, Protocol, cast

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._sockets import create_site

from .errors import InvalidRouterRequestError, RouterError


class RouterStrategy(Protocol):
    def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]: ...

    def discard(self, session_id: str) -> None: ...

    def clear(self) -> None: ...


_STRATEGY_KEY: web.AppKey[object] = web.AppKey("router_strategy", object)
_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def create_router_app(*, strategy: RouterStrategy) -> web.Application:
    """Build the aiohttp application without starting a transport site."""

    app = web.Application(client_max_size=100 * 1024 * 1024)
    app[_STRATEGY_KEY] = strategy
    app.router.add_post("/chat/completions", handle_chat_completions)
    return app


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    """Run one Router strategy and proxy its validated SSE response."""

    logger.info("Received experimental Router request")
    try:
        raw_body = await request.json()
    except Exception as error:
        return _http_error(status=400, message=str(error), error_type="invalid_request_error")
    if not isinstance(raw_body, dict):
        return _http_error(
            status=400,
            message="Request body must be a JSON object",
            error_type="invalid_request_error",
        )
    body: dict[str, Any] = raw_body
    try:
        _validate_request_body(body)
    except InvalidRouterRequestError as error:
        return _http_error(status=400, message=str(error), error_type="invalid_request_error")

    strategy = cast(RouterStrategy, request.app[_STRATEGY_KEY])
    stream = strategy.stream(body=body)
    response = web.StreamResponse(status=200, reason="OK", headers=_SSE_HEADERS)
    async with aclosing(stream) as events:
        try:
            await response.prepare(request)
        except Exception as error:
            logger.warning(f"Router client disconnected before SSE response prepared: {error!r}")
            return response

        try:
            saw_event = False
            async for event in events:
                saw_event = True
                await _write_event(response=response, event=event)
            if not saw_event:
                raise RouterError("Router strategy returned no completion events")
            await response.write(b"data: [DONE]\n\n")
        except ConnectionResetError:
            _discard_session_state(strategy=strategy, body=body)
            logger.info("Router client disconnected")
        except Exception as error:
            _discard_session_state(strategy=strategy, body=body)
            logger.error(f"Router stream failed after response prepare: {error!r}")
            await _write_sse_error(response=response, error=error)
        return response


async def serve_router(*, session_socket: str, strategy: RouterStrategy) -> None:
    """Serve one Router strategy until externally cancelled."""

    logger.info(f"Starting experimental Router on {session_socket}")
    app = create_router_app(strategy=strategy)
    runner = web.AppRunner(app, handler_cancellation=True)
    try:
        await runner.setup()
        site = create_site(runner, session_socket)
        await site.start()
    except anyio.get_cancelled_exc_class():
        strategy.clear()
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise
    except Exception as error:
        logger.error(f"Failed to start experimental Router on {session_socket}: {error}")
        strategy.clear()
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"Experimental Router listening on {session_socket}")
    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down experimental Router on {session_socket}")
        strategy.clear()
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"Experimental Router shutdown complete on {session_socket}")


async def _write_event(*, response: web.StreamResponse, event: dict[str, Any]) -> None:
    choices = event.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RouterError("Router strategy events must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise RouterError("Router strategy choice must be an object")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        raise RouterError("Router strategy choice delta must be an object")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise RouterError("Router strategy finish reason must be a string or null")
    encoded = json.dumps(event, ensure_ascii=False)
    logger.debug(f"Experimental Router outgoing SSE chunk: {encoded[:1000]}")
    await response.write(f"data: {encoded}\n\n".encode())


async def _write_sse_error(*, response: web.StreamResponse, error: Exception) -> None:
    event = {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": f"[Router Error]: {error}"},
                "finish_reason": "error",
            }
        ],
    }
    try:
        await _write_event(response=response, event=event)
    except Exception as write_error:
        logger.warning(f"Failed to send Router SSE error: {write_error!r}")


def _http_error(*, status: int, message: str, error_type: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": error_type, "param": None, "code": status}},
        status=status,
    )


def _validate_request_body(body: dict[str, Any]) -> None:
    messages = body.get("messages")
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
        raise InvalidRouterRequestError("messages must be a list of objects")
    tools = body.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise InvalidRouterRequestError("tools must be a list of objects")
    if body.get("stream", True) is not True:
        raise InvalidRouterRequestError("Router service requires stream=true")
    routing = body.get("routing")
    if routing is not None and not isinstance(routing, dict):
        raise InvalidRouterRequestError("routing must be an object when present")
    session_id = routing.get("session_id") if isinstance(routing, dict) else None
    if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
        raise InvalidRouterRequestError("routing.session_id must be a non-empty string")


def _discard_session_state(*, strategy: RouterStrategy, body: dict[str, Any]) -> None:
    routing = body.get("routing")
    session_id = routing.get("session_id") if isinstance(routing, dict) else None
    if isinstance(session_id, str) and session_id.strip():
        strategy.discard(session_id.strip())


__all__ = ["RouterStrategy", "create_router_app", "handle_chat_completions", "serve_router"]
